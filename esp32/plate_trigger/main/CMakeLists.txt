idf_component_register(SRCS "main.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp32_p4_eye esp_wifi esp_event esp_netif esp_http_client nvs_flash driver esp_driver_jpeg)
