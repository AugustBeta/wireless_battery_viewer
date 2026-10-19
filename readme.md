# 无线外设电量（Windows 10/11）

一个只用 **Python 标准库 + ctypes + tkinter** 的无线外设电量小工具。

- 简洁面板：每张卡片显示设备名、大号电量、进度条、数据来源与更新时间。
- 托盘常驻：通知区域按设备显示数字电量图标；右键打开/前置面板，左键立即刷新。
- 完整调试：枚举所有 HID 设备/接口、转储 Feature Report、探测 Report ID、试发命令、监听 Input Report、保存手动标定。
- 开机自启：启动文件夹里的 `电量托盘.lnk`，任务管理器 →「启动」中可见、可禁用。
- 桌面入口：目录符号链接 + 启动脚本符号链接，不复制脚本。
- 不写日志文件；只有 `--debug` 时向控制台输出。

当前实测设备：

```text
ROG Strix Scope II 96 RX (OMNI)：66%（ASUS OMNI 厂商协议）
```

> 这台机器上的 ROG OMNI 接收器同时提供键盘和鼠标两个 HID 通道，但当前只配对了键盘。
> 未连接的通道不会创建卡片，也不会创建托盘图标；以后配对鼠标后，鼠标卡片和第二个
> 图标会自动出现。

## 快速开始

需要 64 位 Python 3.10+（自带 tkinter）。没有 `pip install` 步骤。

先在项目目录打开命令提示符：

```bat
cd /d F:\code\wireless_battery_viewer
```

常用命令：

```bat
rem 读取一次并打印
python my_devices.py --once

rem 简洁面板 + 托盘常驻
python my_devices.py

rem 只启动托盘（启动快捷方式使用这个）
python my_devices.py --tray

rem 面板 + 完整调试窗口
python my_devices.py --debug-ui

rem 列出所有 HID 接口
python my_devices.py --list-hid

rem 增加控制台调试输出（不写文件）
python my_devices.py --debug

rem 创建启动快捷方式 + 两个桌面链接
python my_devices.py --install

rem 只创建桌面链接 / 只创建启动快捷方式
python my_devices.py --links-only
python my_devices.py --autostart-only

rem 让正在运行的托盘完整退出
python my_devices.py --quit
```

`--once` 的返回码：至少一个设备在线为 `0`；全部离线或没有设备为 `1`。

## 使用说明

### 1. 启动与关闭

- 双击桌面的「启动电量托盘.pyw」：启动简洁面板和托盘。
- 双击桌面的「无线外设电量」目录链接：打开项目目录，方便查看或修改脚本。
- 在项目目录运行 `python my_devices.py`：效果与双击启动脚本相同。
- 开机自启使用 `pythonw.exe`，不会弹出黑色控制台窗口。
- 面板右上角的 `X`、`Esc` 或「隐藏到托盘」都只是**隐藏**，托盘仍然常驻。
- 要真正退出：在面板底部点击「退出托盘」并确认；也可以运行
  `python my_devices.py --quit`。
- 程序是单实例：重复双击或重复执行启动命令时，只会把已打开的面板前置，
  不会创建第二个进程、第二个面板或第三、第四个托盘图标。

### 2. 简洁面板

面板只显示无线设备卡片，每张卡片包含：

- 设备类型（键盘 / 鼠标 / 耳机）和真实型号；
- 大号电量百分比和进度条；
- 充电/在线状态；
- 数据来源（例如「ASUS OMNI 厂商协议」「标准 HID 电量字段」）；
- 最近一次更新时间。

颜色规则：

```text
≥ 60%    绿色
30–59%   黄色
< 30%    红色
无数据    灰色（显示 —）
```

常用操作：

- **立即刷新**：在后台线程读取一次，界面不会卡住。
- **刷新间隔**：在「刷新间隔」输入 `30`–`1800` 秒（即 30 秒到 30 分钟），
  点击「应用」。下一次定时刷新会使用新间隔，并写入配置文件。
- **完整调试**：打开 HID 调试窗口。
- **创建桌面入口**：重新创建/检查桌面目录链接和启动脚本链接。
- **隐藏到托盘**：保留托盘，收起面板。
- **退出托盘**：移除所有设备托盘图标并结束进程。

已配对设备休眠或接收器暂时无响应时，卡片显示「未连接」，不会伪造电量；
唤醒设备后点击「立即刷新」，或等待下一次定时刷新。未配对的接收器通道不会显示。

### 3. 托盘交互

- **左键单击图标**：立即刷新一次。
- **右键单击图标**：打开简洁面板；如果面板已经打开，则将其前置并聚焦。
- **鼠标悬停**：显示「设备名：电量（更新时间）」提示。
- 托盘没有右键菜单，退出入口在简洁面板底部。
- 托盘图标数量与当前已连接设备一致：只连接键盘时只有一个图标；键盘和鼠标都配对时
  会出现两个数字图标。未连接的通道不会创建图标。
- 没有任何已连接设备时，托盘保留一个通用应用图标（显示 `--`）用于重新打开面板，
  但不会显示设备卡片。
- 如果额外的图标被 Windows 收进了通知区域折叠区，点击任务栏的 `^`
  即可看到；也可以在 Windows 设置中把它固定到任务栏。
- Explorer 重启后，程序会自动重新添加图标。

### 4. 开机自启

「开机自启」勾选框与任务管理器 →「启动」使用同一个真实状态：

- 勾选：创建启动文件夹快捷方式，并清除任务管理器的禁用标记。
- 取消勾选：保留快捷方式，但写入任务管理器的禁用标记。
- 如果用户曾经在任务管理器里禁用，重新打开面板时勾选框会显示为未勾选。
- 常驻程序**不会**在启动时自动创建、删除或“修复”启动项；只有点击勾选框或
  显式运行 `--install` 才会修改。

启动快捷方式：

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\电量托盘.lnk
```

当前快捷方式目标：

```text
E:\Lang\Python\pythonw.exe "F:\code\wireless_battery_viewer\my_devices.py" --tray
```

任务管理器中显示名称为「电量托盘」。本机已经创建并启用。

如果移动了项目目录，快捷方式里的旧路径会失效；请重新运行：

```bat
python my_devices.py --install
```

### 5. 桌面入口

桌面上应有两个**链接**，不是脚本副本：

```text
无线外设电量             -> F:\code\wireless_battery_viewer
启动电量托盘.pyw         -> F:\code\wireless_battery_viewer\launch_tray.pyw
```

- 目录优先创建真正的符号链接；权限不足时退回 `mklink /J` junction。
- 启动脚本优先符号链接；失败时退回 `os.link` 硬链接。
- 如果桌面已有同名普通文件，程序会拒绝覆盖。
- 只重建桌面入口：`python my_devices.py --links-only`。
- 移动项目后，旧链接会失效；重新运行 `--install` 或点击面板里的
  「创建桌面入口」。

### 6. 完整调试窗口

打开方式：面板底部「完整调试」，或运行 `python my_devices.py --debug-ui`。

1. 在 HID 列表中选中一个接口，右侧「接口详情」会显示路径、VID:PID、
   usage page/usage、Input/Output/Feature 报告长度和可访问状态。
2. **Feature Report**：输入 Report ID（十进制或 `0x` 十六进制），点击
   「转储 Feature Report」；或点击「探测 Report ID 0–255」。
   如果显示 `FeatureReportByteLength=0`，说明该接口没有 Feature Report。
3. **试发命令**：Report ID 单独填写，Payload 只填报告数据（十六进制，
   不要重复填写 Report ID）。发送后窗口会显示 1 秒内收到的原始报文。
4. **监听 Input**：输入监听秒数，点击开始；结束后显示收到的 Input Report。
5. **手动标定**：选择接口后填写名称、Report ID、命令、电量字节下标、倍率、
   充电下标、包长度和接口匹配子串，点击「保存标定并刷新」。
   标定优先级最低，只对没有被标准 HID/厂商协议占用的接口生效。

调试输出只显示在调试窗口或 `--debug` 控制台，不会写日志文件。

### 7. 配置文件

设置和手动标定保存在：

```text
%LOCALAPPDATA%\WirelessBatteryViewer\config.json
```

结构示例：

```json
{
  "version": 1,
  "refresh_seconds": 60,
  "calibrations": [
    {
      "name": "My Mouse",
      "vendor_id": 8483,
      "product_id": 1234,
      "path_contains": "mi_02&col03",
      "report_id": 3,
      "battery_offset": 5,
      "scale": 1.0,
      "command": [18, 7],
      "charging_offset": 10,
      "charging_value": 1,
      "packet_size": 64,
      "kind": "mouse"
    }
  ]
}
```

这是用户设置文件，不是日志。删除它只会恢复默认设置和标定，不会影响程序运行。

## 文件结构

```text
my_devices.py              CLI / 单实例 / 启动器
launch_tray.pyw            桌面链接目标（pythonw，不弹黑窗）
wireless/
  winhid.py                SetupAPI / hid.dll / HID 读写 / Overlapped 读句柄
  providers.py             ASUS OMNI、标准 HID 电量、HID++ 适配器、手动标定
  repository.py            后台刷新线程，向托盘/界面发布 Snapshot
  models.py                DeviceTarget / DeviceReading / Snapshot
  tray.py                  Win32 隐藏窗口 + Shell_NotifyIcon + SetTimer
  icons.py                 用 3x5 像素字体现场绘制数字 HICON
  config.py                JSON 设置、启动快捷方式、桌面链接
  app.py                   Tk 主线程与托盘线程的协调
  ui.py                    简洁面板
  debug_ui.py              完整调试窗口
```

## 数据与协议

每个设备的读取优先级：

1. 标准 HID 电量字段：Generic Device Controls usage page `0x0006` / usage `0x0020`。
2. 厂商协议表：当前完整实现 ASUS；也预留 Endgame `0x3367`、VAXEE `0x3057`、
   Nordic `0x1915` 的发现钩子。
3. Logitech HID++ 2.0：已实现 `0x1000` / `0x1001` / `0x1004` 查询适配器，
   只对显式注册的 Logitech 目标执行，避免把罗技接收器里的每个集合都误报成设备。
4. 监听 Input Report：标准字段读取失败时的兜底通道，可在调试窗口查看原始报文。
5. 手动标定：最后兜底，把 Report ID、命令、电量字节下标、倍率写入 JSON。

### ASUS OMNI（0B05:1ACE）

接收器的 `mi_02&col01` 通道返回配对列表：

```text
OUT: 01 A0 00 00
IN : 01 A0 00 01 00 7A 1B 02 04 ...
                 ^^^^^ 0x1B7A = ROG Strix Scope II 96 RX
```

- 键盘通道 `mi_02&col02`，Report ID `0x02`，命令 `12 01`，电量在响应 `[6]`，
  充电状态在 `[9]`，数据包 64 字节。
- 鼠标通道 `mi_02&col03`，Report ID `0x03`，命令 `12 07`，电量在响应 `[5]`，
  充电状态在 `[10]`，数据包 64 字节。
- 接收器偶尔会在配对列表查询后对下一条命令返回 `FF AA`（NAK）；读取器会静默重试一次。
- 如果鼠标未配对，该通道不会被发现，也不会显示卡片或托盘图标。

型号映射及独立无线键盘/耳机也定义在 `wireless/providers.py` 的表中，新增型号只需加一条
`AsusSpec`。

## 开机自启与桌面链接

启动快捷方式位置：

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\电量托盘.lnk
```

快捷方式目标：

```text
E:\Lang\Python\pythonw.exe "F:\code\wireless_battery_viewer\my_devices.py" --tray
```

创建完全使用 ctypes COM vtable（`CoCreateInstance` → `IShellLinkW` →
`IPersistFile::Save`），没有 pywin32。任务管理器 →「启动」中显示名称「电量托盘」。

「开机自启」勾选框只读取真实状态：

- 快捷方式存在；
- 目标是当前 `pythonw.exe` 且参数指向当前脚本；
- 未被任务管理器写入 `StartupApproved\StartupFolder` 的禁用标记。

常驻程序启动时**不会**自动创建或删除启动项。只有用户点击勾选框或显式运行
`--install` 才会修改。点击取消勾选时写入任务管理器的禁用标记，保留启动项；
再次勾选时清除标记。这样界面和任务管理器看到的是同一个状态。

桌面链接（使用 Windows 已知文件夹，不在 OneDrive 时也能正确定位）：

```text
桌面\无线外设电量           -> F:\code\wireless_battery_viewer   （符号链接，失败退 junction）
桌面\启动电量托盘.pyw       -> F:\...\launch_tray.pyw             （符号链接，失败退 os.link 硬链接）
```

`--install` 是幂等的；如果同名位置已有非链接文件，程序会拒绝覆盖。

## 调试功能细节

「完整调试」列出每个 HID 接口的 VID:PID、usage page/usage、Input/Output/Feature
报告长度、MI/COL 和句柄可访问状态。

- **Feature Report**：用 `dwDesiredAccess=0` 的句柄调用 `HidD_GetFeature`，可转储单个
  Report ID 或扫描 0–255；系统独占的键盘/鼠标集合也能读。
- **试发命令**：输入 Report ID 与 payload（hex）。发送使用独立 write 句柄，响应由独立
  `FILE_FLAG_OVERLAPPED` read 句柄接收，超时调用 `CancelIoEx`。
- **监听 Input**：独立 overlapped 读句柄，监听指定秒数，不读写同一个句柄。
- **手动标定**：填写名称、Report ID、命令、电量字节下标、倍率、充电下标、包长度和接口
  匹配子串，保存后立即刷新。手动标定优先级最低，只对尚未被标准/厂商协议占用的接口生效。

操作步骤见前文「使用说明 → 6. 完整调试窗口」；调试输出只显示在窗口或 `--debug`
控制台，不写日志文件。

## 已规避的 Windows 坑

- **独立读句柄**：厂商命令从不使用同一个 HID 句柄阻塞 `ReadFile` 再并发
  `WriteFile`/IOCTL。write 句柄只写；read 句柄带 `FILE_FLAG_OVERLAPPED`，每次读有事件
  超时，超时调用 `CancelIoEx` 并等待取消完成。
- **系统独占集合**：元数据和 Feature Report 一律用 `dwDesiredAccess=0` 打开。
- **托盘退出**：`WM_CLOSE` 通过 Tk 队列走完整退出流程；真正销毁窗口后在
  `WM_DESTROY` 中调用 `PostQuitMessage(0)`。外部 `--quit` 也走同一路径，不会留下
  「窗口没了但进程还在」的僵尸。
- **Tk 线程安全**：后台线程只 `queue.Queue.put` 和 `PostMessageW`；Tk 主线程用
  `after(100, pump)` 取结果。托盘线程绝不直接调用 Tk。
- **图标更新**：刷新只 `NIM_MODIFY`；只有删除时才 `NIM_DELETE`，重新出现时一定重新
  `NIM_ADD`。Explorer 重启后监听 `TaskbarCreated` 并全部重新添加。
- **DPI**：启动时优先 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`，失败回退
  `SetProcessDPIAware()`；数字图标按 `SM_CXSMICON` 实际尺寸绘制。
- **单实例**：命名 Mutex + 隐藏托盘窗口。第二次启动通过 `WM_APP_SHOW` 让已有实例前置
  面板，不创建第二个窗口。
- **无日志**：没有任何 `logging` 文件处理器；只有 `--debug` 时的控制台输出。

## 验收结果（本机实测）

- `python my_devices.py --once`：只输出已连接的键盘，电量 `66%`；未配对的鼠标通道不显示。
- 面板只显示一张键盘卡片（单卡片居中）；键盘卡绿色，阈值代码为 ≥60 绿、30–59 黄、<30 红。
- 托盘只注册一个设备图标（`Shell_NotifyIconGetRect` 对 uid 1 成功，uid 2 失败）；
  键盘图标实测显示数字 `66`。
- 右键托盘回调使隐藏面板 `IsWindowVisible=True`；左键回调触发一次 `refresh requested:
  tray-left-click`。
- 启动文件夹快捷方式被 `Win32_StartupCommand` 识别为 `电量托盘`，位置 `Startup`。
- 桌面两项 `LinkType=SymbolicLink`，分别指向项目目录和 `launch_tray.pyw`。
- 连续启动/关闭 3 次：每次进程均退出，之后无托盘窗口/面板残留。
- 空闲 75 秒 CPU 增量：`0.047` 秒；`SetTimer` 在 30 秒测试间隔下按时触发属性刷新。

## 已知边界

- OMNI 接收器有两条逻辑通道，但只显示已配对的通道；没有配对鼠标时不会出现空卡片，
  也不会虚报第二个设备。配对鼠标后自动增加鼠标卡片和第二个图标。
- 某些设备（如本机 AURA LED Controller）`FeatureReportByteLength=0`，调试窗口会明确
  提示没有可读 Feature Report。
- 厂商命令可能被系统独占或设备固件拒绝（返回 `FF AA`）；面板会显示未连接，调试窗口会
  显示原始响应。
- 非 ASUS 的自动发现目前只依赖标准 HID 电量字段。Logitech HID++ 适配器和手动标定已
  实现，但不会把接收器里的未知集合自动变成卡片；新增品牌请扩展 `discover_targets`。

## 常见问题与卸载

**键盘显示「—」或「未连接」**

- 先按一下键盘唤醒，再点击面板「立即刷新」或左键单击托盘图标。
- 确认 2.4GHz 接收器插在可用的 USB 口上；如果经过 KVM/USB Hub，尝试直连。
- 运行 `python my_devices.py --once --debug` 查看是否出现 `FF AA`（设备休眠/未响应）；
  调试窗口也能看到原始收发报文。
- 接收器刚查询完配对列表时偶尔会 NAK 一次，程序会自动重试；仍失败则等下一次刷新。

**只看到一个托盘图标**

- 正常：托盘图标数量等于当前已连接设备数。当前只配对了键盘，所以只有一个图标。
- 运行 `python my_devices.py --once` 可以确认当前识别到几台设备。
- 如果键盘和鼠标都已配对，但仍只看到一个图标，点击任务栏的 `^` 展开通知区域，
  或到「设置 → 个性化 → 任务栏 → 其他系统托盘图标」中固定。
- 未配对的接收器通道不会创建图标。

**开机自启没有生效**

- 打开面板查看「开机自启」是否勾选；如果任务管理器里显示「已禁用」，勾选框会同步为
  未勾选，重新勾选即可清除禁用标记。
- 也可以运行 `python my_devices.py --install` 重新创建并启用启动项。
- 如果移动过项目目录，快捷方式仍指向旧路径；重新运行 `--install`。
- 程序不会在后台自动修复启动项，避免覆盖用户在任务管理器里的选择。

**调试输出在哪里**

- `--debug` 只输出到控制台，不写日志文件；用 `pythonw` 运行时该参数会创建调试控制台。
- 图形化的原始报文、Feature Report 和 Input Report 都在「完整调试」窗口中。

**如何彻底卸载**

1. 在面板中点击「退出托盘」，确认进程结束。
2. 取消「开机自启」；或直接在任务管理器中禁用/删除「电量托盘」。
3. 删除桌面的「无线外设电量」目录链接和「启动电量托盘.pyw」脚本链接。
4. 可选：删除 `%LOCALAPPDATA%\WirelessBatteryViewer\config.json` 以清除设置和手动标定。
5. 删除项目目录。

本程序没有 Windows 服务、没有注册表 `Run` 启动项，也没有后台计划任务。

