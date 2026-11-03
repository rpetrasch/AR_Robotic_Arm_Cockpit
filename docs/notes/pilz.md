# Pilz Industrial Motion Planner

## Intro
The Pilz Industrial Motion Planner is a deterministic motion planning plugin for the MoveIt and MoveIt 2 robotics frameworks. It generates predictable, repeatable industrial robot trajectories like those found on commercial controllers from brands like KUKA or ABB. References:
- https://moveit.picknik.ai/main/doc/how_to_guides/pilz_industrial_motion_planner/pilz_industrial_motion_planner.html
- https://moveit.ai/moveit/pilz/motion%20planner/2020/12/17/Pilz-Plugin-for-MoveIt.html)
- https://robocloud-dashboard.vercel.app/learn/blog/moveit-2-pilz-cartesian-motion-planning
- https://www.reddit.com/r/robotics/comments/1w14g57/ros2_pick_place_ompl_vs_pilz_what_would_you/

## Core Motion Primitives

Unlike stochastic or sampling-based planners (such as OMPL) that look for collision-free paths around clutter, the Pilz planner uses standard industrial motion commands with trapezoidal velocity profiles (see https://moveit.picknik.ai/main/doc/how_to_guides/pilz_industrial_motion_planner/pilz_industrial_motion_planner.html, https://www.reddit.com/r/robotics/comments/1w14g57/ros2_pick_place_ompl_vs_pilz_what_would_you/):
- PTP (Point-to-Point): Moves joints directly from a start position to a goal position.
- LIN (Linear Cartesian): Moves the robot's Tool Center Point (TCP) along a straight line in 3D Cartesian space while synchronizing rotation via quaternion slerp.
- CIRC (Circular Arc): Moves the TCP along a circular arc trajectory (see https://docs.ros.org/en/melodic/api/moveit_tutorials/html/doc/pilz_industrial_motion_planner/pilz_industrial_motion_planner.html), https://moveit.picknik.ai/main/doc/how_to_guides/pilz_industrial_motion_planner/pilz_industrial_motion_planner.html).

## Key Features

- Deterministic Behavior: Produces the exact same predictable path for a given start and goal state every single time, which is vital for validated industrial setups( see https://www.blackcoffeerobotics.com/blog/moveit-motion-planning-how-ompl-chomp-and-pilz-planners-actually-behave-on-ros2, https://www.reddit.com/r/robotics/comments/1w14g57/ros2_pick_place_ompl_vs_pilz_what_would_you/).
- Blending / Continuous Motion: Supports blend_radius parameters via sequence requests (MotionSequenceRequest) so the robot can seamlessly transition through multiple motion segments without stopping at intermediate target points (see https://developer.mamezou-tech.com/en/robotics/pilz/pilz/, https://moveit.picknik.ai/main/doc/how_to_guides/pilz_industrial_motion_planner/pilz_industrial_motion_planner.html).
- Interfaces: Accessible via C++, Python (pilz_robot_programming), ROS action/service interfaces, or RViz (see https://moveit.picknik.ai/main/doc/how_to_guides/pilz_industrial_motion_planner/pilz_industrial_motion_planner.html, https://developer.mamezou-tech.com/en/robotics/pilz/pilz/).
- Limitations: It is not designed to navigate around dynamic or unexpected obstacles; it assumes a structured environment and will either fail or collide if blocked (see https://automaticaddison.com/difference-between-pilz-stomp-and-ompl-planners/, https://www.blackcoffeerobotics.com/blog/moveit-motion-planning-how-ompl-chomp-and-pilz-planners-actually-behave-on-ros2).
