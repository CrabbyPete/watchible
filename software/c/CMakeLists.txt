cmake_minimum_required(VERSION 3.13)

# initialize the SDK directly
include(..//pico-sdk/pico_sdk_init.cmake)

project(my_project)

# initialize the Raspberry Pi Pico SDK
pico_sdk_init()


add_executable(hello
    hello.c
    queue.c
)

pico_enable_stdio_uart(hello 1)

# Add pico_stdlib library which aggregates commonly used features
target_link_libraries(hello pico_stdlib hardware_adc )

# create map/bin/hex/uf2 file in addition to ELF.
pico_add_extra_outputs(hello)
