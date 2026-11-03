cmake_minimum_required(VERSION 3.16)
project(ar3_cockpit_agent)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})
install(PROGRAMS scripts/cockpit_agent DESTINATION lib/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_health_unit test/test_health_unit.py TIMEOUT 60)
  ament_add_pytest_test(test_app_unit test/test_app_unit.py TIMEOUT 60)
  ament_add_pytest_test(test_telemetry_unit test/test_telemetry_unit.py TIMEOUT 60)
  ament_add_pytest_test(test_scenario_unit test/test_scenario_unit.py TIMEOUT 60)
  # The launch test with a running stack lives in ar3_bringup (test_cockpit_agent_mock.py).
endif()

ament_package()
