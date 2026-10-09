# 私有维护说明（FaintGhost fork）

本仓库是 [XiaoMi/ha_xiaomi_home](https://github.com/XiaoMi/ha_xiaomi_home) 的个人维护分支，
用于在家用 HA（core-2026.9.4）上修复自己遇到的问题，同时定期同步上游。

## 分支策略

| 分支 | 用途 |
|---|---|
| `main` | 上游镜像，只从 upstream 同步，**不直接改** |
| `custom-fixes` | 实际使用的分支 = 上游 + 自己的修复，HA 部署用这个 |

## 当前包含的私有修复

### 1. BLE Mesh 设备「假离线」自愈（基于 v0.5.0）

现象：路由器更换/断网后，BLE Mesh 设备（如紫米开关）在 HA 里永久 unavailable，
但米家 App 显示在线。原因：云端对 BLE Mesh 设备的上下线推送不可靠，漏一条就永久卡死。

改动（commit `fix: re-sync cloud device state...`，补丁文件 `xiaomi_home_resync_fix.patch`）：

- `miot/miot_client.py`
  - `__refresh_cloud_devices_async` 成功后每 1800s 定时全量刷新设备列表，漏掉的上线通知可自愈；
  - 云 MQTT 重连时，向云端已报告在线的设备补发 ONLINE，实体立即刷新。
- `miot/miot_device.py`
  - 设备已在线时再收到状态变化事件，仍然刷新属性（覆盖状态推送丢失的场景）。

## 同步上游的流程

```bash
cd xiaomi_home_src
git fetch upstream
git checkout main && git merge --ff-only upstream/main && git push origin main
git checkout custom-fixes
git merge main            # 或 git rebase main；有冲突就手工解
# 解决冲突后验证：
python -m py_compile custom_components/xiaomi_home/miot/miot_client.py \
                     custom_components/xiaomi_home/miot/miot_device.py
git push origin custom-fixes
```

如果上游已修复相同问题，直接用 `git checkout main -- <文件>` 丢弃对应私有改动。

## 部署到 HA

HA 是 HA OS，配置目录经 Samba 挂载（`//192.168.50.86/config`）。

```bash
# 在本仓库 custom-fixes 分支下，用 smbprotocol 或资源管理器
# 把 custom_components/xiaomi_home 整个目录覆盖到 HA 的 /config/custom_components/
# 然后重启 HA（改的是 Python 代码，reload 不够）
```

升级前先在 HA 里对集成做个备份，升级后检查：
集成 loaded、关键实体在线、日志无 xiaomi_home 报错。

## 给上游提 PR

`custom-fixes` 上的修复尽量写成上游能接受的独立 commit，
确认稳定后可以直接从本 fork 向 XiaoMi/ha_xiaomi_home 提 PR。
