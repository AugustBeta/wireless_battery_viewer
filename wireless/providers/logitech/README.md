# Logitech 电量获取

当前支持 Logitech G PRO 系列无线耳机（`046D:0AA7`、`046D:0AAA`、`046D:0ABA`）。
代码入口是 `provider.py` 的 `discover()` 和 `read()`，由
`wireless/providers/registry.py` 注册到统一调度中。

## 排查过程

本机设备为 `PRO X Wireless Gaming Headset`（`046D:0ABA`），有三个 HID 接口：

| 接口 | Usage | 输入/输出/Feature | 结论 |
| --- | --- | --- | --- |
| `MI_03 / COL_01` | `000C/0001` | 5 / 0 / 0 | Consumer Control，无电量 |
| `MI_03 / COL_02` | `FF43/0202` | 20 / 20 / 0 | 电量查询实际使用该接口 |
| `MI_03 / COL_03` | `FF00/0001` | 64 / 64 / 64 | Feature Report 只有 `FF`，无电量 |

依次排除了以下路径：

1. **HID++ 2.0 电量特性**：在 `FF43/0202` 上查询 `0x1000`、`0x1001`、`0x1004`，
   接收器均返回特性索引 `0`，表示不支持；改按设备索引 `0x01..0x06` 查询也失败。
2. **Feature Report**：`FF00/0001` 的 Feature Report 只返回 `ff 01 00...`，没有电量。
3. **Input Report 被动监听**：监听 4 秒没有收到电量报文。
4. **Windows PnP 电池属性**：设备节点没有可用的电池属性。

最终参考 HeadsetControl 的 `logitech_gpro.c`：G PRO 系列使用 20 字节的 HID++ 长报告，
通过厂商命令读取电池电压，而不是标准 HID 电量字段或 HID++ 电池特性。

## 协议

向 `FF43/0202` 发送 20 字节长报告，前四字节为命令，其余补零：

```text
OUT: 11 FF 06 0D 00 00 ...
IN : 11 FF 06 0D 10 3C 03 ...
                ^^^^^ ^^
                电压   状态
```

- `11`：HID++ 长报告。
- `FF`：寻址接收器。
- `06 0D`：G PRO 电池电压查询。
- 响应 `[4]`、`[5]` 是**大端**电压，例如 `0x103C` = `4156 mV`。
- 响应 `[6] == 0x03` 表示正在充电；`0x01` 表示放电。

电量百分比使用 HeadsetControl 对 G PRO 系列的六项电压拟合曲线，电压有效范围为
`2500–5000 mV`，结果限制在 0–100：

```python
GPRO_BATTERY_TERMS = (
    -1.7790085824253613e006,
    2.3692153307344888e003,
    -1.2580905041506840,
    3.3295201187388395e-004,
    -4.3913981733597497e-008,
    2.3093121045539907e-012,
)
```

`_read_gpro()` 负责发送命令、校验响应长度、解析电压和充电状态；超时返回
「耳机未连接或已休眠」，电压越界返回读取错误。

## 接入方式

`discover()` 只匹配：

```text
VID == 0x046D
PID in {0x0AA7, 0x0AAA, 0x0ABA}
UsagePage == 0xFF43 and Usage == 0x0202
```

匹配后创建 `kind="headset"` 的 `DeviceTarget`，`provider="logitech"`。统一调度
`discovery.read_target()` 会先尝试标准 HID 电量，再交给本提供者的 `read()`。

## 验证

```text
$ python my_devices.py --once
ROG Strix Scope II 96 RX (OMNI)：62%（ASUS OMNI 厂商协议）
PRO X Wireless Gaming Headset：95%（Logitech G PRO 厂商协议）
```

充电时：

```text
IN : 11 ff 06 0d 10 3c 03 ...
→ PRO X Wireless Gaming Headset：100%（Logitech G PRO 厂商协议，充电中）
```

## 边界

- 耳机休眠或未连接时查询会超时，面板显示未连接，不缓存旧电量。
- `0x0AF7`（PRO X 2 LIGHTSPEED）使用不同的帧格式，当前不套用这套逻辑。
- 其它罗技设备保留 HID++ 2.0 读取代码，但尚未接入自动发现；需要时在
  `discover()` 中增加对应 PID 和接口匹配。
