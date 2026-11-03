cmake_minimum_required(VERSION 3.16)
project(ar3_gazebo)

find_package(ament_cmake REQUIRED)

install(DIRECTORY config launch worlds test DESTINATION share/${PROJECT_NAME})

# The Gazebo integration test (test/test_sim.py) needs rendering and several
# minutes, so it is not registered with ctest; run it with `make test-sim`.

ament_package()
