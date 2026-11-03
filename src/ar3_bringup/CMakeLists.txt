cmake_minimum_required(VERSION 3.16)
project(ar3_bringup)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})

install(DIRECTORY config launch DESTINATION share/${PROJECT_NAME})
install(PROGRAMS scripts/move_to_named DESTINATION lib/${PROJECT_NAME})

# The site configuration lives at the repository root (config/, SPEC §8). It is
# installed here so launch files find it without $AR3_CONFIG_DIR; with
# `colcon build --symlink-install` edits to config/ take effect without a rebuild.
set(AR3_SITE_CONFIG "${CMAKE_CURRENT_SOURCE_DIR}/../../config")
if(NOT EXISTS "${AR3_SITE_CONFIG}/robot.yaml")
  message(FATAL_ERROR "Site config not found at ${AR3_SITE_CONFIG}")
endif()
install(DIRECTORY "${AR3_SITE_CONFIG}/" DESTINATION share/${PROJECT_NAME}/config/site)

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  find_package(launch_testing_ament_cmake REQUIRED)
  ament_add_pytest_test(test_named_poses_unit test/test_named_poses_unit.py TIMEOUT 60)
  ament_add_pytest_test(test_launch_args test/test_launch_args.py TIMEOUT 60)
  add_launch_test(test/test_bringup_mock.py TIMEOUT 300)
  add_launch_test(test/test_cockpit_agent_mock.py TIMEOUT 300)
endif()

ament_package()
