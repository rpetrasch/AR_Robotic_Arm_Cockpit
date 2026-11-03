# ar3_moveit_config

MoveIt 2 configuration for the AR3 workcell. The SRDF is generated from the site config,
because the bins come from `bins.yaml`.

- Planning groups: `arm` (chain `base_link → gripper_tcp`) and `gripper` (`gripper_jaw1_joint`; jaw 2 is a mimic joint).
- Planners: OMPL (RRTConnect, the default) and Pilz industrial motion planner (PTP/LIN/CIRC).
- Default velocity/acceleration scaling: taken from `config/robot.yaml` `motion:` (0.2).

| Named pose | Group | Purpose |
|---|---|---|
| `home` | arm | All joints 0 |
| `observe` | arm | TCP ~0.40 m above the workspace centre, tool and wrist camera pointing down |
| `stow` | arm | Folded back over the base, out of the workspace |
| `open` / `closed` | gripper | Jaw 14 mm / 0 mm |

The Python module `ar3_moveit_config` provides `build_moveit_config(config_dir, hardware,
controllers_file)`, `move_group_node()` and `rviz_node()`. All launch files use it.

```bash
ros2 launch ar3_moveit_config move_group.launch.py rviz:=true   # robot already running
```
