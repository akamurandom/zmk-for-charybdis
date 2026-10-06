# Build patched copies without changing the west checkout or its cache.
if(CONFIG_CHARYBDIS_VBUS_POWER)
  if(NOT CONFIG_ZMK_RGB_UNDERGLOW_AUTO_OFF_IDLE OR CONFIG_ZMK_RGB_UNDERGLOW_AUTO_OFF_USB)
    message(FATAL_ERROR "VBUS policy requires AUTO_OFF_IDLE=y and AUTO_OFF_USB=n")
  endif()

  find_package(Git REQUIRED)
  set(vbus_sources "${CMAKE_CURRENT_BINARY_DIR}/vbus-sources")
  file(MAKE_DIRECTORY "${vbus_sources}")
  foreach(source activity.c rgb_underglow.c)
    configure_file("${APPLICATION_SOURCE_DIR}/src/${source}" "${vbus_sources}/${source}" COPYONLY)
    # The app adds its original sources after Zephyr loads this module.
    set_source_files_properties("${APPLICATION_SOURCE_DIR}/src/${source}"
      DIRECTORY "${APPLICATION_SOURCE_DIR}" PROPERTIES HEADER_FILE_ONLY TRUE)
  endforeach()
  execute_process(
    COMMAND "${GIT_EXECUTABLE}" apply --no-index
      "${CMAKE_CURRENT_LIST_DIR}/patches/zmk-v0.3-vbus.patch"
    WORKING_DIRECTORY "${vbus_sources}"
    RESULT_VARIABLE patch_result
    OUTPUT_VARIABLE patch_output
    ERROR_VARIABLE patch_error)
  if(NOT patch_result EQUAL 0)
    message(FATAL_ERROR "ZMK sources are incompatible with the VBUS patch:\n${patch_output}${patch_error}")
  endif()
  set_property(DIRECTORY "${APPLICATION_SOURCE_DIR}" APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_LIST_DIR}/patches/zmk-v0.3-vbus.patch")
  target_include_directories(app PRIVATE "${CMAKE_CURRENT_LIST_DIR}/include")
  target_sources(app PRIVATE "${vbus_sources}/activity.c" "${vbus_sources}/rgb_underglow.c")
endif()
