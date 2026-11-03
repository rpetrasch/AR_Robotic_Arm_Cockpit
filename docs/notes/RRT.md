# RRT (Rapidly-exploring Random Tree)

## Intro
**Rapidly-exploring Random Trees (RRT)** is a sampling-based path planning algorithm designed to find collision-free paths in high-dimensional continuous search spaces. For robotic arms, RRT searches the robot's **Configuration Space (C-space)**—where each dimension corresponds to a degree of freedom (DOF) or joint angle—rather than operating directly in 3D Cartesian space.

---

## Why RRT for Robotic Arms?

Robotic manipulators often have 6, 7, or more degrees of freedom ($n$-DOF). Classical grid-based search algorithms (like $A^*$ or Dijkstra) fail in high dimensions due to the exponential growth of grid cells (the curse of dimensionality).

RRT solves this by:

* **Avoiding explicit C-space obstacle construction:** Calculating exact 3D obstacle boundaries in joint space is computationally intractable. RRT only requires a point-wise collision checker.
* **Probabilistic Completeness:** If a valid path exists, the probability that RRT will find it approaches 1 as the number of iterations approaches infinity.
* **Voronoi Bias:** RRT rapidly expands toward unexplored regions of the configuration space by favoring large un-sampled volumes.

---

## The Standard RRT Algorithm Steps

An RRT tree $T$ is initialized with the starting joint configuration $q_{\text{start}} = [\theta_1, \theta_2, \dots, \theta_n]$.

```
Algorithm RRT:
1. T.init(q_start)
2. for i = 1 to N_iter:
3.     q_rand = SampleState()
4.     q_near = NearestNeighbor(T, q_rand)
5.     q_new  = Steer(q_near, q_rand, delta_q)
6.     if CollisionFree(q_near, q_new):
7.         T.add_node(q_new)
8.         T.add_edge(q_near, q_new)
9.         if Distance(q_new, q_goal) < threshold:
10.            return ReconstructPath(T, q_new)

```

### 1. Goal-Biased Sampling ($q_{\text{rand}}$)

A random joint vector $q_{\text{rand}}$ is picked uniformly at random within joint limit bounds. To speed up convergence, algorithms typically use **goal biasing**: with a small probability $p_{\text{goal}}$ (e.g., 5% to 10%), $q_{\text{rand}}$ is set directly to $q_{\text{goal}}$.

### 2. Nearest Neighbor Search ($q_{\text{near}}$)

The algorithm searches the existing tree $T$ for the node $q_{\text{near}}$ closest to $q_{\text{rand}}$ using a distance metric in joint space:

$$D(q_a, q_b) = \sqrt{\sum_{i=1}^{n} w_i (\theta_{a,i} - \theta_{b,i})^2}$$

*Weights ($w_i$) are often assigned to give higher priority to base/shoulder joints over wrist joints, as base movements cause larger physical displacements.*

### 3. Steering Step ($q_{\text{new}}$)

Instead of jumping straight to $q_{\text{rand}}$, the system extends from $q_{\text{near}}$ toward $q_{\text{rand}}$ by a maximum step size $\Delta q$:

$$q_{\text{new}} = q_{\text{near}} + \min\left(1, \frac{\Delta q}{\Vert{}q_{\text{rand}} - q_{\text{near}}\Vert{}}\right) (q_{\text{rand}} - q_{\text{near}})$$

### 4. Collision Checking

Forward kinematics is evaluated along the linear segment connecting $q_{\text{near}}$ to $q_{\text{new}}$.

* If the robot links or end-effector collide with the environment or self-collide, $q_{\text{new}}$ is discarded.
* If the trajectory segment is valid, $q_{\text{new}}$ is added to $T$ with an edge to $q_{\text{near}}$.

---

## Key Challenges in Robotic Arm Applications

| Challenge | Cause | Common Solutions |
| --- | --- | --- |
| **Narrow Passages** | Trajectories requiring precise alignment (e.g., reaching inside a cabinet) have tiny valid C-space volumes. | Bi-directional RRT, Medial Axis sampling, active region biasing. |
| **Jagged Paths** | Random sampling leads to unnatural, jerky joint movement. | Post-processing via shortcutting (path pruning) and spline smoothing (B-splines, cubic polynomials). |
| **Collision Bottlenecks** | Repeated 3D CAD mesh overlap checks eat up computation time. | Hierarchical bounding volumes (OBB/AABB trees), GPU-accelerated collision engines (FCL, Flexible Collision Library). |
| **Joint Limit / Wrap-around** | Revolute joints near $\pm\pi$ require topology-aware distance metrics. | Computing distances on the $n$-torus manifold $S^1 \times S^1 \dots \times S^1$. |

---

## Popular RRT Variants for Manipulators

* **RRT-Connect (Bi-RRT):** Grows two trees simultaneously—one from $q_{\text{start}}$ and one from $q_{\text{goal}}$—and tries to step them toward each other. This is the default planner in standard robotics frameworks like MoveIt (ROS).
* **RRT* (Optimal RRT):** Re-wires existing tree branches in the local neighborhood of $q_{\text{new}}$ to guarantee asymptotic optimality (converges to the shortest path in joint space over time).
* **Kinodynamic RRT:** Incorporates velocity, acceleration, and torque constraints ($\ddot{q}$, $\tau$) into the `Steer` function to generate executable trajectories for heavy arms without exceeding motor limits.
* **Informed RRT*:** Constrains random sampling to an $n$-dimensional ellipsoid after an initial solution is found, drastically speeding up optimization.

---

# Remarks on MoveIt2

MoveIt 2 natively supports RRT and its variants through its integration with the Open Motion Planning Library (OMPL). OMPL is the default motion planning plugin for MoveIt 2, providing out-of-the-box implementations of RRT, RRT-Connect, RRT*, and related algorithms.

---

## Key RRT Variants Available in MoveIt 2

| Planner Name in MoveIt 2 | Description | Best Used For |
| --- | --- | --- |
| **RRTConnect** (`RRTConnectkConfigDefault`) | **Default planner in MoveIt 2.** Grows two trees simultaneously (from start and goal). | High-speed, unconstrained point-to-point motion planning in clutter. |
| **RRT** (`RRTkConfigDefault`) | Standard single-tree Rapidly-exploring Random Tree implementation. | Basic sampling, baseline comparison. |
| **RRT* / RRTstar** (`RRTstarkConfigDefault`) | Asymptotically optimal RRT that rewires the tree to find shortest path. | Path-length optimization where extra execution time is acceptable. |
| **InformedRRTstar** (`InformedSG`) | Directs sampling into an ellipsoidal heuristic region once an initial solution is found. | Faster path convergence than standard RRT*. |
| **TRRT** (Transition-based RRT) | Uses dynamic domain control to navigate complex costmaps or terrain features. | Motion planning with continuous state cost functions (e.g., elevation, clearance). |

---

## How MoveIt 2 Configures RRT

In MoveIt 2, planners are configured via the `ompl_planning.yaml` configuration file within your robot's MoveIt configuration package.

### Configuration Example (`ompl_planning.yaml`)

```yaml
arm:
  planner_configs:
    - RRTConnectkConfigDefault
    - RRTstarkConfigDefault
  
  default_planner_config: RRTConnectkConfigDefault

planner_configs:
  RRTConnectkConfigDefault:
    type: geometric::RRTConnect
    range: 0.0  # Max step size (0.0 auto-calculates based on C-space extent)
    
  RRTstarkConfigDefault:
    type: geometric::RRTstar
    range: 0.0
    goal_bias: 0.05
    delay_collision_checking: 1

```

---

## Usage in C++ / Python API

When using MoveIt 2's `MoveGroupInterface` or `moveit_py`, you can explicitly set the planner ID to any registered RRT variant:

```cpp
// C++ MoveGroupInterface
move_group.setPlannerId("RRTConnectkConfigDefault");
move_group.setPlanningTime(5.0); // Allow up to 5 seconds to solve

```

```python
# Python (moveit_py)
arm.set_planner_id("RRTConnectkConfigDefault")
arm.plan()

```

---

## When to Use Alternatives in MoveIt 2

While RRT-Connect is the default choice for general obstacle avoidance, MoveIt 2 provides alternative planning backends for specific use cases:

* **Linear / Cartesian Straight Lines:** Use the **Pilz Industrial Motion Planner** plugin (`LIN` or `PTP` commands) instead of RRT, as pure RRT produces non-straight, jagged end-effector paths.
* **Trajectory Optimization:** Use **STOMP** or **CHOMP** plugins if smooth, minimum-jerk trajectories with explicit optimization metrics are required without post-processing shortcut steps.