# existing project

add_executable(xapp_demo
    xapp_demo.c
    ${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c
)

target_link_libraries(xapp_demo
    PUBLIC
    e42_xapp
    pthread
    sctp
    dl
)
