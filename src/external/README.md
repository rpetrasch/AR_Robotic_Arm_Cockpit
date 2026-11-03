# Vendored upstream code

## ar4_ros_driver

| | |
|---|---|
| Upstream | https://github.com/ycheng517/ar4_ros_driver (marked unmaintained) |
| Pinned commit | `4b88db1c924515f7ce343105d46a637ecf2d7659` (2026-04-04, "fix calibration speed, MODEL initialization issue") |
| License | MIT (`ar4_ros_driver/LICENSE`) |
| Import | Plain copy of the tree at that commit, without `.git`, `.vscode`, `.dockerignore` |

Packages: `annin_ar4_description`, `annin_ar4_driver`, `annin_ar4_firmware`,
`annin_ar4_gazebo`, `annin_ar4_moveit_config`. All of them build in the
workspace. The AR3 stack currently uses:

- `annin_ar4_description`: AR4 MK1 link meshes and the servo gripper macro
  (`urdf/ar_gripper_macro.xacro`), included by `ar3_description`.

The AR3 arm macro (`src/ar3_description/urdf/ar3_macro.xacro`) is a parameterized
copy of upstream `ar_macro.xacro` that lives in our own package, so it is not a
divergence of the vendored tree.

### Divergences from upstream

None. Every change to files under `src/external/ar4_ros_driver/` must be listed
here (file, what, why).

### Updating

```bash
git clone https://github.com/ycheng517/ar4_ros_driver /tmp/ar4 && git -C /tmp/ar4 checkout <commit>
rsync -a --delete --exclude .git --exclude .vscode --exclude .dockerignore /tmp/ar4/ src/external/ar4_ros_driver/
# re-apply the divergences above, update the pinned commit, run `make test`
```
