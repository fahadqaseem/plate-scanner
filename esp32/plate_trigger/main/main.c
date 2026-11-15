/* ESP32-P4-EYE plate trigger:
 *   3.3 V pulse on a header GPIO (from FREE-WILi) OR Boot button
 *   -> capture one VGA frame from the MIPI-CSI camera -> HW JPEG encode
 *   -> HTTP POST image/jpeg to CONFIG_PT_SERVER_URL.
 */
#include <string.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "driver/jpeg_encode.h"
#include "esp_http_client.h"
#include "esp_rom_crc.h"
#include "driver/usb_serial_jtag.h"
#include <stdio.h>
#include "linux/videodev2.h"
#include "esp_video_init.h"
#include "bsp/esp-bsp.h"

static const char *TAG = "plate";

#define CAM_W 640
#define CAM_H 480
#define NBUF  2

/* ---------------- Wi-Fi ---------------- */
static EventGroupHandle_t s_ev;  /* only used when PT_POST_ENABLE */
#define GOT_IP BIT0

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_ev, GOT_IP);
        ESP_LOGW(TAG, "Wi-Fi lost, retrying");
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(s_ev, GOT_IP);
    }
}

static void wifi_start(void)
{
    s_ev = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));
    wifi_config_t wc = { 0 };
    strlcpy((char *)wc.sta.ssid, CONFIG_PT_WIFI_SSID, sizeof wc.sta.ssid);
    strlcpy((char *)wc.sta.password, CONFIG_PT_WIFI_PASS, sizeof wc.sta.password);
    wc.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());
}

/* ---------------- Camera ---------------- */
static int      s_fd = -1;
static uint8_t *s_buf[NBUF];
static size_t   s_buf_len[NBUF];
static uint32_t s_w = CAM_W, s_h = CAM_H;

static esp_err_t camera_start(void)
{
    ESP_ERROR_CHECK(bsp_i2c_init());
    i2c_master_bus_handle_t i2c;
    ESP_ERROR_CHECK(bsp_get_i2c_bus_handle(&i2c));

    esp_video_init_csi_config_t csi = {
        .sccb_config = { .init_sccb = false, .i2c_handle = i2c, .freq = 100000 },
        .reset_pin = -1,
        .pwdn_pin = -1,
    };
    esp_video_init_config_t vc = { .csi = &csi };
    ESP_ERROR_CHECK(esp_video_init(&vc));

    s_fd = open("/dev/video0", O_RDONLY);
    if (s_fd < 0) { ESP_LOGE(TAG, "open /dev/video0 failed"); return ESP_FAIL; }

    /* Same order as Espressif's factory demo: read the sensor's native size,
     * then change only the pixel format. Forcing VGA is rejected by this sensor. */
    struct v4l2_format f = { .type = V4L2_BUF_TYPE_VIDEO_CAPTURE };
    if (ioctl(s_fd, VIDIOC_G_FMT, &f) != 0) { ESP_LOGE(TAG, "G_FMT failed"); return ESP_FAIL; }
    ESP_LOGI(TAG, "native %" PRIu32 "x%" PRIu32 " fmt=0x%" PRIx32,
             f.fmt.pix.width, f.fmt.pix.height, (uint32_t)f.fmt.pix.pixelformat);
    if (f.fmt.pix.pixelformat != V4L2_PIX_FMT_RGB565) {
        struct v4l2_format g = { .type = V4L2_BUF_TYPE_VIDEO_CAPTURE,
            .fmt.pix = { .width = f.fmt.pix.width, .height = f.fmt.pix.height,
                         .pixelformat = V4L2_PIX_FMT_RGB565 } };
        if (ioctl(s_fd, VIDIOC_S_FMT, &g) != 0) { ESP_LOGE(TAG, "S_FMT RGB565 failed"); return ESP_FAIL; }
    }
    s_w = f.fmt.pix.width; s_h = f.fmt.pix.height;
    ESP_LOGI(TAG, "camera %" PRIu32 "x%" PRIu32 " RGB565", s_w, s_h);

    struct v4l2_requestbuffers rq = { .count = NBUF, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE,
                                      .memory = V4L2_MEMORY_MMAP };
    if (ioctl(s_fd, VIDIOC_REQBUFS, &rq) != 0) { ESP_LOGE(TAG, "REQBUFS failed"); return ESP_FAIL; }
    for (int i = 0; i < NBUF; i++) {
        struct v4l2_buffer b = { .type = V4L2_BUF_TYPE_VIDEO_CAPTURE,
                                 .memory = V4L2_MEMORY_MMAP, .index = i };
        if (ioctl(s_fd, VIDIOC_QUERYBUF, &b) != 0) { ESP_LOGE(TAG, "QUERYBUF failed"); return ESP_FAIL; }
        s_buf[i] = mmap(NULL, b.length, PROT_READ | PROT_WRITE, MAP_SHARED, s_fd, b.m.offset);
        s_buf_len[i] = b.length;
        if (!s_buf[i]) return ESP_FAIL;
        if (ioctl(s_fd, VIDIOC_QBUF, &b) != 0) { ESP_LOGE(TAG, "QBUF failed"); return ESP_FAIL; }
    }
    int t = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(s_fd, VIDIOC_STREAMON, &t) != 0) { ESP_LOGE(TAG, "STREAMON failed"); return ESP_FAIL; }
    return ESP_OK;
}

/* Dequeue one frame (blocking). Caller must give it back with frame_put(). */
static bool frame_get(struct v4l2_buffer *b)
{
    memset(b, 0, sizeof *b);
    b->type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    b->memory = V4L2_MEMORY_MMAP;
    return ioctl(s_fd, VIDIOC_DQBUF, b) == 0;
}
static void frame_put(struct v4l2_buffer *b) { ioctl(s_fd, VIDIOC_QBUF, b); }

/* ---------------- JPEG + upload ---------------- */
static jpeg_encoder_handle_t s_jpeg;
static uint8_t *s_out;
static size_t   s_out_cap;

static esp_err_t jpeg_setup(void)
{
    jpeg_encode_engine_cfg_t ec = { .intr_priority = 0, .timeout_ms = 200 };
    ESP_ERROR_CHECK(jpeg_new_encoder_engine(&ec, &s_jpeg));
    jpeg_encode_memory_alloc_cfg_t mc = { .buffer_direction = JPEG_ENC_ALLOC_OUTPUT_BUFFER };
    s_out = jpeg_alloc_encoder_mem(s_w * s_h * 2, &mc, &s_out_cap);
    return s_out ? ESP_OK : ESP_ERR_NO_MEM;
}


/* Wire output: "PLATEJPG" | u32 len (LE) | JPEG bytes | u32 CRC32 (LE) of the JPEG.
 * Logs share this port, so the reader scans for the magic and checks the CRC. */
static void usb_write_all(const uint8_t *p, size_t n)
{
    while (n) {
        int w = usb_serial_jtag_write_bytes(p, n > 1024 ? 1024 : n, pdMS_TO_TICKS(2000));
        if (w <= 0) return;           /* host not reading: give up, reader will drop the frame */
        p += w; n -= w;
    }
}

static void serial_send(const uint8_t *jpg, uint32_t len)
{
    static const uint8_t magic[8] = { 'P','L','A','T','E','J','P','G' };
    uint32_t crc = esp_rom_crc32_le(0, jpg, len);
    usb_write_all((const uint8_t *)"\n", 1);
    usb_write_all(magic, 8);
    usb_write_all((const uint8_t *)&len, 4);
    usb_write_all(jpg, len);
    usb_write_all((const uint8_t *)&crc, 4);
}

static void capture_and_send(void)
{
    struct v4l2_buffer b;
    /* Drop frames queued before the trigger so the picture is fresh. */
    for (int i = 0; i < 6; i++) {   /* also lets auto-exposure settle */
        if (!frame_get(&b)) return;
        frame_put(&b);
    }
    if (!frame_get(&b)) return;

    jpeg_encode_cfg_t jc = {
        .src_type = JPEG_ENCODE_IN_FORMAT_RGB565,
        .sub_sample = JPEG_DOWN_SAMPLING_YUV420,
        .image_quality = CONFIG_PT_JPEG_QUALITY,
        .width = s_w, .height = s_h,
    };
    uint32_t jlen = 0;
    esp_err_t e = jpeg_encoder_process(s_jpeg, &jc, s_buf[b.index], s_w * s_h * 2,
                                       s_out, s_out_cap, &jlen);
    frame_put(&b);
    if (e != ESP_OK) { ESP_LOGE(TAG, "jpeg encode failed: %s", esp_err_to_name(e)); return; }
    ESP_LOGI(TAG, "jpeg %" PRIu32 " bytes", jlen);

#if CONFIG_PT_SERIAL_OUT
    serial_send(s_out, jlen);
#endif
#if !CONFIG_PT_POST_ENABLE
    return;
#endif
    if (!(xEventGroupGetBits(s_ev) & GOT_IP)) { ESP_LOGW(TAG, "no Wi-Fi, frame dropped"); return; }

    esp_http_client_config_t hc = {
        .url = CONFIG_PT_SERVER_URL,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 8000,
    };
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    esp_http_client_set_header(c, "Content-Type", "image/jpeg");
    esp_http_client_set_post_field(c, (const char *)s_out, jlen);
    e = esp_http_client_perform(c);
    if (e == ESP_OK) ESP_LOGI(TAG, "POST -> HTTP %d", esp_http_client_get_status_code(c));
    else             ESP_LOGE(TAG, "POST failed: %s", esp_err_to_name(e));
    esp_http_client_cleanup(c);
}

/* ---------------- Triggers ---------------- */
static QueueHandle_t s_q;

static void IRAM_ATTR trig_isr(void *arg)
{
    BaseType_t hp = pdFALSE;
    uint32_t src = (uint32_t)arg;
    xQueueSendFromISR(s_q, &src, &hp);
    if (hp) portYIELD_FROM_ISR();
}

/* Laptop trigger: the byte 'T' on the USB port captures one image. */
static void cmd_task(void *arg)
{
    uint8_t c;
    while (1) {
        if (usb_serial_jtag_read_bytes(&c, 1, portMAX_DELAY) == 1 && c == 'T') {
            uint32_t src = 3;
            xQueueSend(s_q, &src, 0);
        }
    }
}

static void triggers_setup(void)
{
    s_q = xQueueCreate(8, sizeof(uint32_t));
    gpio_config_t in = {
        .pin_bit_mask = 1ULL << CONFIG_PT_TRIGGER_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,   /* idle low, pulse high */
        .intr_type = GPIO_INTR_POSEDGE,
    };
    ESP_ERROR_CHECK(gpio_config(&in));
    gpio_config_t btn = {
        .pin_bit_mask = 1ULL << CONFIG_PT_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,       /* active low */
        .intr_type = GPIO_INTR_NEGEDGE,
    };
    ESP_ERROR_CHECK(gpio_config(&btn));
    usb_serial_jtag_driver_config_t uc = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    uc.tx_buffer_size = 4096; uc.rx_buffer_size = 256;
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&uc));
    xTaskCreate(cmd_task, "cmd", 3072, NULL, 5, NULL);
    ESP_ERROR_CHECK(gpio_install_isr_service(0));
    ESP_ERROR_CHECK(gpio_isr_handler_add(CONFIG_PT_TRIGGER_GPIO, trig_isr, (void *)1));
    ESP_ERROR_CHECK(gpio_isr_handler_add(CONFIG_PT_BUTTON_GPIO, trig_isr, (void *)2));
}

void app_main(void)
{
    esp_err_t r = nvs_flash_init();
    if (r == ESP_ERR_NVS_NO_FREE_PAGES || r == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        r = nvs_flash_init();
    }
    ESP_ERROR_CHECK(r);

#if CONFIG_PT_POST_ENABLE
    wifi_start();
#endif
    esp_err_t ce = camera_start();
    if (ce == ESP_OK) ce = jpeg_setup();
    if (ce != ESP_OK) {
        ESP_LOGE(TAG, "camera/jpeg init failed: %s", esp_err_to_name(ce));
        vTaskDelay(portMAX_DELAY);
    }
    triggers_setup();
    ESP_LOGI(TAG, "ready: trigger GPIO%d (rising) or button GPIO%d",
             CONFIG_PT_TRIGGER_GPIO, CONFIG_PT_BUTTON_GPIO);

    uint32_t src;
    while (1) {
        if (xQueueReceive(s_q, &src, portMAX_DELAY) != pdTRUE) continue;
        ESP_LOGI(TAG, "trigger from %s", src == 1 ? "gpio" : src == 2 ? "button" : "usb");
        capture_and_send();
        /* Swallow bounce / the rest of the pulse and anything during the upload. */
        vTaskDelay(pdMS_TO_TICKS(CONFIG_PT_DEBOUNCE_MS));
        xQueueReset(s_q);
    }
}
