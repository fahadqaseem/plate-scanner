add_executable(platescan_display display/main.c)
target_link_libraries(platescan_display fwog_display_bsp)
target_include_directories(platescan_display PRIVATE ${CMAKE_CURRENT_LIST_DIR})
fwog_display_app(platescan_display
    VERSION 001
    DESCRIPTION "Plate scanner trigger: green button sends a 200 ms pulse on breakout GPIO 27")

add_executable(platescan_main main/main.c)
target_link_libraries(platescan_main fwog_main_bsp)
target_include_directories(platescan_main PRIVATE ${CMAKE_CURRENT_LIST_DIR})
fwog_embed_display_image(platescan_main)
fwog_main_app(platescan_main
    VERSION 001
    DESCRIPTION "Plate scanner trigger: drives breakout GPIO 27 high for 200 ms on a green-button press")

if(PLATESCAN_SELFTEST)
    target_compile_definitions(platescan_main PRIVATE PLATESCAN_SELFTEST=1)
endif()
