# 私有维护说明（FaintGhost fork）

本仓库是 [XiaoMi/ha_xiaomi_home](https://github.com/XiaoMi/ha_xiaomi_home) 的个人维护分支，
用于在家用 HA（core-2026.9.4）上修复自己遇到的问题，同时定期同步上游。

### 3. 云 MIPS 重连竞态（v0.5.10）

现象：系统日志偶发一条 ERROR：

```
ERROR custom_components.xiaomi_home.miot.miot_mips: __on_connect, but mqtt is disconnected
ERROR custom_components.xiaomi_home.miot.miot_client: mips try reconnect after 20s
```

原因：云 MQTT 断开（`rc=7` = `MQTT_ERR_CONN_LOST`）后重连，CONNACK 到达时连接又被对端关掉，
`__on_connect` 里的 `is_connected()` 为 False。原实现把它当错误，直接 `return`，
既不重置退避也不记账，于是白白多等一个翻倍的退避周期。

改动（`miot/miot_mips.py` 的 `__on_connect`）：

- 该分支降为 `info`：`__on_connect, connection closed during connect, {rc}`；
- 重置 `_mips_reconnect_interval`，重连按最小间隔（10s）而不是翻倍退避；
- 增加 `client is not self._mqtt` 判断，忽略已被重建的旧客户端回调。

上层逻辑不变：真正的重连仍由 `on_disconnect` 调度，发布/订阅仍各自检查 `is_connected()`。

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

### 2. 私有 URN 设备扩展（v0.5.9）

背景：部分设备（多路插座、私有功能开关等）的附加能力使用**厂商私有 spec URN**，
比如 `urn:cuco-spec:service:switch:...` 而不是 `urn:miot-spec-v2:service:switch:...`。
`miot/miot_spec.py` 会把这类服务/属性标记成 `proprietary`，一旦开启
「隐藏非标准实体」（`hide_non_standard_entities: true`，默认值），这些实体
就完全不创建，导致附加插孔在 HA 里不可用。

改动：

- `miot/specs/spec_standard.yaml`（新增）：按**设备 URN** 列出需要强制视为标准的
  `services` / `properties` / `events` / `actions`。格式与 `spec_filter.yaml` 相同，
  语义相反。
- `miot/miot_spec.py`：新增 `_SpecStandard` 类（仿 `_SpecFilter`），并在
  `__parse` 计算 `proprietary` 时增加豁免判断。

新增一台设备支持的做法：

1. 在 `.storage/xiaomi_home/miot_specs/` 里找到该设备的 spec 缓存
   （`urn:miot-spec-v2:device:...:<model>:<ver>_<lang>.dict`），查看目标服务的
   `type` 是否为私有 URN；
2. 在 `spec_standard.yaml` 里以 **URN 前 6 段**为 key（不含末尾版本号）添加条目，
   siid/piid 用**字符串**写法：`"3"`、`"3.1"`、`"3.*"`；
3. **删除对应的 spec 缓存文件**并重启 Home Assistant（解析结果有缓存，
   只 reload 集成不会重新解析）；
4. 验证：目标实体出现且可用。

注意：`hide_non_standard_entities` 保持 `true` 即可，不需要为了一台设备全局放开
（全局放开会让所有设备的非标准实体一起冒出来）。

### 3. AInice 电子围栏传感器完整适配（v0.5.13）

背景：`ainice.sensor_occupy.bt`（电子围栏传感器）的大量能力挂在
`urn:ainice-spec:` 私有服务上：siid 3 是通用服务（指示灯、蓝牙配对、
蓝牙功率、组网、设备状态列表、唤醒指令），siid 4/5..8 是设备群与四个成员
设备（在线状态、在线/离线时长、离线判定时长、上线方式、追踪模式、进群/退群）。
开启「隐藏非标准实体」后这些全部消失，只剩在线状态和一个成员计数。

改动：

- `miot/specs/spec_standard.yaml`：放行 siid 3、4、5-8 以及动作 `3.1`
  （`send-magic-package`，链路丢失时的唤醒指令）。9-18 是未启用的空槽，
  不放行，避免空槽也生成一堆实体。
- `miot/specs/spec_filter.yaml`：排除私密/无用属性——`3.3` 蓝牙配对密码、
  `3.11` 激活日期、`3.12` 缓存版本、`4.4` 和各成员 `*.8` 绑定信息。
- `miot/miot_spec.py`：`_SpecStandard` 增加 `is_action_standard()`，
  `__parse` 里动作的 proprietary 判断改为走放行名单（原实现是
  `spec_service.proprietary or True`，永远为真，导致放行服务里的动作仍被丢弃）。
- `miot/specs/spec_filter.yaml` 另外排除两个纯诊断属性：
  - `3.9` 组网是否连通：未启用组网，恒为 0，且会与通用 sensor 生成重复实体；
  - `3.10` 设备状态列表：每成员 4 个字段、变化频繁，做成实体会不停写状态历史，
    需要时用 `pyscript.ainice_status` 从云端按需读取快照。
- `miot/miot_device.py`：
  - `append_prop()` 改为幂等：同一属性对象重复进入转换流程时只保留在一个平台列表里
    （spec 二次转换会让同一 unique_id 生成两个实体）。
  - `parse_miot_ainice_property()`：把这些 uint8 属性映射成 switch
    （指示灯/蓝牙配对/组网开关/进群退群）和 binary_sensor
    （组网连通，device_class=connectivity）；其余走通用转换
    （`bt-power-level` 等变 select，`offline-interval`/`mesh-id` 变 number，
    状态列表与在线/离线时长变 sensor）。
  - `_coerce_binary()`：switch / binary_sensor 平台是用 `_value is True`
    判断的，而厂商私有属性是 uint8，读回来是 1/0 会永远显示 off；这里在
    读取路径把 int 归一成 bool。写入路径不变（`value_precision` 对 int 属性
    会把 bool 处理成 1/0）。

部署注意：解析结果有缓存，改完必须**删除该设备的 spec 缓存**
（`.storage/xiaomi_home/miot_specs/urn:miot-spec-v2:device:occupancy-sensor:0000A0BF:ainice-bt:2_zh-Hans.dict`）
并重启，否则缓存里的 `proprietary: true` 会让改动不生效。

效果（该设备从 17 个实体扩到 45 个）：
- `switch.*_indicator_switch/_bt_pair_switch/_mesh_switch/_joined_group`
- `binary_sensor.*_mesh_reachable`、`select.*_bt_power_level/_mesh_type/_online_mode/_bt_capture_mode`
- `number.*_offline_interval`（把默认 30 秒的离线判定改成 600 秒，直接消掉抖动误判）
- `sensor.*_status_list`（14 个成员各 4 个字段的原始状态串，排障用）
- `notify.*_send_magic_package_a_3_1`（唤醒：`notify.send_message` 传成员序号；
  走 MIPS 本地转发，实测 `{"code":0,"out":[]}`）

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

### 2. 日志降噪（基于 v0.5.0）

BLE 设备经网关推送的属性/事件用老式数字名（4100/4102/4106），映射不到 MIoT spec
会被丢弃——功能无影响（实体靠轮询更新），但每条都写 INFO 日志，几秒一条刷屏。
改动：miot_mips.py 中 `unknown prop msg` / `unknown event msg` / `wrong event msg`
三处从 `log_info` 降为 `log_debug`。

### 3. 事件实体 unavailable 弹跳修复（基于 v0.5.0）

现象：每次集成重载 / HA 重启 / 中枢网关或云 mips 断线重连时，`event.*` 实体
（无线开关 click 等）状态从「上次事件时间戳」跳变为 `unavailable` 再跳回，
from=时间戳→to=unavailable 的 state 触发器被误触发（灯被误 toggle）。

改动：

- `miot/miot_client.py` `__load_cache_device_async`
  - 加载缓存设备列表时保留上次在线状态，不再强制 `online=False`。上游强制离线
    导致每次重载/重启所有实体先 unavailable、等云端/网关刷新后才恢复在线。
    真正离线的设备仍会被加载后的首次设备刷新标记为离线。
- `miot/miot_device.py` `MIoTEventEntity.__on_device_state_changed`
  - 事件实体不再因设备离线而被标记 unavailable（其状态是历史事件时间戳，
    弹跳只会误触发自动化）；设备在线状态仍可看其它实体。ONLINE 转换仍然生效，
    启动时就离线的实体恢复在线后能变回 available。

### 4. HA 2026.10 启动日志治理（基于 v0.5.0）

三项均不改变设备控制逻辑：

- 门铃 event 实体合规（`event.py`）：HA core 要求 `device_class=doorbell` 的
  event 实体 `event_types` 必须包含 `ring`（2027.4 起停止工作）。仅给门铃事件的
  `event_types` 列表追加 `ring`，触发时实际发出的 event_type 仍是 spec 翻译名，
  已有自动化不受影响。
- device_tracker 废弃属性（`device_tracker.py` + `miot/specs/specv2entity.py`）：
  core 废弃 `TrackerEntity` 的 `battery_level` 和 `location_name`
  （`_attr_location_name` 同样废弃，2027.7 起不支持）。删除两个 property
  override；watch 的 battery 服务不再并入 tracker，`battery-level` 回落为独立的
  电量 sensor 实体（core 推荐方向）。对外变化：tracker 失去 `battery_level`
  属性（电量改看新 sensor）、state 不再用 area-id 当位置名（改由经纬度+zone 推导）。
- 日志降噪（`miot/miot_device.py`）：`new miot service/property/event entity`
  三条从 INFO 降为 DEBUG（同 54289bb 风格），一次重启少 200+ 条，
  消除 core 的 logging-too-frequently 警告。

### 5. 电热毯 A/B 分区档位（基于 v0.5.0）

- `number.py`：为 `custom.a-b-gears` 拆出 `A区档位` / `B区档位` 两个 number 实体。
  写入时保留另一区当前档位，再按 `"A-B"` 格式回写，范围 0–6；
  0 表示关闭当前区域。
- 仅影响电热毯（`xiaomi.blanket.mj3`），不改变原有全局档位/开关逻辑。

### 6. HA 2026.11 温度 API 迁移（基于 v0.5.0）

消除 HA 2026.11 的 `helpers.deprecation` 温度属性警告，2027.11 前兼容：

- `climate.py`：`_attr_temperature_unit` → `_attr_native_temperature_unit`；
  `FeatureTargetTemperature.target_temperature` → `native_target_temperature`；
  `FeatureTemperature.current_temperature` → `native_current_temperature`。
- `water_heater.py`：同样迁移 `_attr_temperature_unit`、`current_temperature`、
  `target_temperature` 到 `native_*`。

### 7. MIPS 断线自愈（基于 v0.5.0）

- 连续 5 次 MIPS 连接失败后重建 paho MQTT Client，不再无限复用可能卡死的旧 client。
- 连接成功时清零失败计数；失败日志记录 host、port、异常和 DNS 解析地址。
- 解决网络短暂异常后必须重启 HA 才能恢复 Xiaomi Home 控制的问题。
- 注意：如果运营商/中间网络持续封锁 `8883`，代码无法绕过，只能改网络出口。

### 8. 电热毯关闭时 A/B 显示归零（基于 v0.5.0）

- A/B 分区档位实体订阅电热毯 `heater.on` 状态。
- 总控关闭时两个滑条显示 0，不再残留上次的 3 档。
- 不向设备写入 `a-b-gears`：实测关闭状态写入会重新开启电热毯，因此只做状态显示同步。

### 9. 电热毯跨设备清零与陈旧缓存修复（v0.5.8）

- 删除 `Switch.async_turn_off` 中遍历所有 A/B number 并写零的逻辑；关闭其他小米设备不再触碰电热毯分区，也不再等待 5 秒。
- A/B 共用写锁，每次写入先读取设备实际的完整档位，保留另一分区后写回；成功后同步两区缓存。
- 一个分区负责原生轮询并刷新两区，覆盖设备不推送档位变化的情况。总控关闭仅令滑条显示 0，不清空硬件分区。
- 回归：`uv run --no-project python test/check_blanket_zones.py`，不安装 HA、不连接真实设备。
- 同一补丁已在家中 HA 持久化并热应用；实际只读轮询已观察到读取 `6-6` 并更新两区。没有调档、切换电热毯总控或重启 HA。

## 发版流程（Marketplace 自定义仓库）

HA 2026.11 起 HACS 并入内置 **Marketplace**；已有 HACS 会自动迁移，自定义仓库和 update 实体保留。
本 fork 已作为 Marketplace 自定义仓库接入家里的 HA，发版即可在 Marketplace 里收到更新提示。

当前维护分支沿用已发布的 `v0.5.x` 版本递增（如 `v0.5.7` → `v0.5.8`）。
发版时保持 manifest、Git tag 和 GitHub Release 的版本一致。不要用 `-custom.x` 后缀，
语义化版本里那是预发布，排序比正式版低，Marketplace 不认。

```bash
git checkout custom-fixes
# 1. 改代码、提交修复
# 2.  bump manifest 版本号
#    编辑 custom_components/xiaomi_home/manifest.json 的 "version" 字段
git add <本次改动文件> && git commit -m "chore: release vX.Y.Z"
# 3. 打 tag 并推送
git tag -a vX.Y.Z -F <发布说明临时文件>
git push origin custom-fixes vX.Y.Z
# 4. 发 release（Marketplace 以 release 为准）
gh release create vX.Y.Z --repo FaintGhost/ha_xiaomi_home --title "vX.Y.Z" --notes-file <发布说明临时文件> --verify-tag
```

HA 侧更新：**设置 → Marketplace → 找到 Xiaomi Home（FaintGhost fork）→ Update → 重启 HA**。

首次接入方式：Marketplace → **Add from link** →
URL `https://github.com/FaintGhost/ha_xiaomi_home`，类型 Integration → 添加。
若之前装的是官方仓库版本，先在 Marketplace 里对官方条目 Remove（只删代码目录，
配置和实体都在 .storage 里不受影响），再从 fork 条目 Download，重启 HA。

## 部署到 HA（无 Marketplace 的手动方式，备用）

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
