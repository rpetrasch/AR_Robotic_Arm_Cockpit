# ar3_gazebo

Gazebo Harmonic world and bridges, included by `ar3_bringup bringup.launch.py mode:=sim`.

- `worlds/workcell.sdf` contains physics, light, the floor and six objects: red, green and blue cubes (30 mm) and cylinders (Ø30 × 40 mm). The table, bins and robot come from the URDF.
- `config/bridge.yaml` bridges gz to ROS for `/clock`, `/cam_overhead/{image_raw,camera_info}` and `/cam_wrist/{image_raw,camera_info}`. Camera resolution, FOV and rate come from `config/cameras.yaml`.
- `launch/gazebo.launch.py` takes `gui:=true|false`, `world:=<sdf>` and `render_engine:=ogre2|ogre`. The engine applies to server and GUI and overrides the `<render_engine>` in the world file. `ogre` is refused without `DISPLAY`.
- `test/test_sim.py` is a
headless integration test. It checks that the camera topics publish and that every named pose is reached. Run it with `make test-sim`; it is not part of CI.
