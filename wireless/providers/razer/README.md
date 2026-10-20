# Razer 电量协议

本目录实现雷蛇无线鼠标的厂商 Feature Report 电量查询，参考 FerrisPulse
项目的 `RazerDeviceHelper` 协议。当前验证设备：

```text
Razer Viper V3 Pro
VID:PID  1532:00C1
接口     MI_00 / COL_00
Feature  91 字节
```

## 为什么不走标准 HID / HID++

- 标准 HID Battery Strength（`0x0006/0x0020`）在该设备的 Feature/Input
  报告中没有可用的电量字段。
- 雷蛇接收器不是 Logitech HID++ 设备，`0x1000/0x1001/0x1004` 不适用。
- Windows PnP 电池属性也不提供该鼠标的电量。

因此使用雷蛇自己的 90/91 字节 Feature Report 命令。

## 报告布局

FeatureReportByteLength 为 90 时，协议从字节 0 开始；为 91 时，Windows
缓冲区前面多一个 Report ID 字节，协议整体偏移 1：

```text
偏移(相对协议起点)
[0]      0
[1]      事务号 transaction id
[5]      参数长度，电量/充电查询固定为 2
[6]      命令类 class = 0x07
[7]      命令 command
[8..9]   参数，查询时补 0
[88]     CRC = XOR [2..87]
[89]     0
```

电量查询：

```text
class=0x07  command=0x80  dataSize=0x02
```

充电查询：

```text
class=0x07  command=0x84  dataSize=0x02
```

事务号依次尝试 `0x1F`、`0x3F`、`0xFF`。接收器可能回显上一次的事务号，
因此只校验 `status=0x02`、class 和 command；原始电量为 `0` 时按无效响应处理。

## 响应解析

```text
[offset + 0]  状态，0x02 表示成功
[offset + 1]  事务号
[offset + 6]  class，应为 0x07
[offset + 7]  command，应为 0x80 或 0x84
[offset + 9]  第一个参数字节
```

电量原始值是 `0–255`：

```text
percent = round(raw / 255 * 100)
```

雷蛇固件把 `0` 视为异常值，界面最低显示 `1%`。充电查询的
`[offset + 9] == 1` 表示正在充电；充电查询失败时仍返回电量，只是不显示
充电状态。

本机实测：

```text
OUT: 00 02 1f 00 00 00 02 07 80 00 00 ...
IN : 00 02 1f 00 00 00 02 07 80 00 5a ...
                                      ^^ raw=90 -> 35%

充电查询 IN: ... 07 84 00 00 ...
```

实测电量在 `35%–44%` 之间变化，充电状态均能正确识别。

## 实现位置

- `provider.py`：发现、PID 归一化、构造报告、CRC、解析和重试。
- `wireless/winhid.py::exchange_feature_report()`：在同一个
  `dwDesiredAccess=0` 句柄上执行 `SetFeature -> sleep -> GetFeature`，
  避免分两次打开句柄丢失响应。

## 边界

- 只发现 `FeatureReportByteLength >= 90` 且可访问的 `0x1532` 接口。
- 同一款设备的有线版和接收器版 PID 会归一化去重，避免插着数据线时出现
  两张卡片。
- 设备休眠或接收器无响应时，所有事务号都拿不到有效响应，读取结果标记为
  「未连接」，不会伪造电量。
- 目前只验证了 Viper V3 Pro；其它型号即使使用同一套 Feature Report 布局，
  也需要实机确认。
