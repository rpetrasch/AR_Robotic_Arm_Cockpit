cmake_minimum_required(VERSION 3.16)
project(ar3_moveit_config)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})

install(DIRECTORY config launch rviz srdf
  DESTINATION share/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_moveit_config test/test_moveit_config.py TIMEOUT 120)
endif()

ament_package()
