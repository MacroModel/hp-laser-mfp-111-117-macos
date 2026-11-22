# HP Laser MFP 111-117 for macOS

社区开发的 Apple Silicon Mac 驱动，实测机型为 **HP Laser MFP 115nw**。从 WPS、预览等应用经 Wi-Fi 打印，可选 600 × 600 或 1200 × 1200 dpi。手动双面只需提交一次，第一趟结束后整叠放回纸盒，按机身键续印。配套的 HP 115nw Studio 提供扫描、复印和 OCR。

[下载 Apple Silicon 安装包（GitHub Releases）](https://github.com/MacroModel/hp-laser-mfp-111-117-macos/releases/latest)

## 安装

1. 在 Apple Silicon Mac 上安装 Homebrew 的 Python 3.13（`brew install python@3.13`）。确保 Mac 与打印机在同一网络，并已在系统设置中添加 115nw 的 AirPrint 队列。
2. 下载发布页 **Assets** 中以 `-arm64.zip` 结尾的安装包，解压后双击 **安装.command**。包内附有已编译的驱动和 Python 依赖。
3. 打开应用的打印窗口，选 **HP Laser MFP 111-117**。安装程序将此队列设为首选，也保留原有的 AirPrint 入口。

## 在 WPS 打印

1. 按 ⌘P，选 **HP Laser MFP 111-117**。
2. 在 **Double-sided** 选 **On** 使用长边翻转，或选 **On (Short Edge)** 使用短边翻转。在 **Printer Options → Printer Features → Print Quality** 选 **600 x 600 dpi** 或 **1200 x 1200 dpi**。提交后也可以在 HP 设置窗口的 **纸张** 和 **双面与布局** 两个标签里核对精度和翻边。
3. 提交后可以在 Mac 的 HP 设置窗口预览页序、调整其他选项或保存预设。点这里的 **打印** 才向设备发送作业。
4. 第一趟结束、打印机停住后，取齐上方的整叠纸，包括空白张。按这台机器已配置的方向原样放回下方纸盘，已印面朝下。**按打印机上的键继续**。Mac 的“知道了”关闭状态提示。

设备计数可用时，Mac 按计数显示两趟进度。计数无法取得或长时间停滞时，可在成品出齐或在机身取消后向 Mac 确认结果。整个双面任务只发送一次。

## 已核对的功能

- **原厂手动双面**。同一作业携带两趟数据。长边 4 页的内容依次为 4、2、1、3，前两页旋转 180°。短边 3 页的内容依次为空白、2、1、3，所有有字的页面正向。51 页时每趟 26 张。也可调整出纸面、放回面和进纸方向。
- **打印设置**。纸张与纸材、份数与逐份、页码范围、页序、缩放、每面多页、小册子、海报、水印、PDF 信头、图像浓淡、省墨和边缘增强，可在打印设置中选择。
- **扫描、复印和 OCR**。打开用户“应用程序”中的 **HP 115nw Studio**。使用 Wi-Fi 平板扫描，可选设备实际公布的 100、200、300 dpi。复印支持浓淡、缩放、多份和身份证正反面合印。可保存 PDF、JPEG、PNG、多页 TIFF 和经 OCR 识别的 PDF、文本或 EPUB。需要多页时，按提示逐张更换原件。

打印通过 Wi-Fi 发送到设备的 9100 端口。扫描使用 eSCL。Mac 端复印由扫描和打印组成。机身独立复印仍按 HP 面板原有方式操作。USB 打印、Windows 的 TWAIN/WIA 接口以及扫描键向电脑推送均未在本包验证。设备在 Wi-Fi 下只公布最高 300 dpi 的扫描请求档位。

## 指定设备与恢复

有多台相同设备或自动识别失败时，可以在安装包目录指定要配对的现有队列和打印机地址。将下列中文参数替换为本机的实际值。

```sh
/usr/bin/python3 setup.py --queue '原有 AirPrint 队列名' --host '打印机主机名或 IPv4 地址'
```

双击 **恢复原队列.command** 可停用本机打印服务并恢复原队列。之前通过本包移出的旧 PDF 菜单会一并恢复。只需恢复旧菜单时可运行 `/usr/bin/python3 setup.py --restore-old-menu`。

## 实机验证

在 Wi-Fi 连接的 HP Laser MFP 115nw 上，从 WPS 提交 58 页 A4 文档、1 份、1200 × 1200 dpi、手动长边双面。机身在第 29 面停下，整叠放回后继续印了 29 面，最终得到 29 张双面成品。已逐页核对送印页序，使用者确认纸面页码和翻转方向正确。大文档在第一张出纸前需要在 Mac 上完成整份排版和编码。

同系列其他机型的实机兼容性仍待验证。

## 从源码构建与离线验证

安装包中的 **离线比对.html** 含两档精度的实际点阵对照和两份原厂双面作业的结果。参考作业来自 Windows 的 **HP Laser MFP 111-117** 文件输出队列。已比对单面 600 dpi、单面 1200 dpi、长边 4 页 600 dpi 和短边 3 页 600 dpi。

从 GitHub 源码构建需 Xcode Command Line Tools、Go 1.26.3 或更高版本及 Homebrew Python 3.13。在项目目录执行以下命令。构建后可双击 **安装.command**，也可先运行下方离线测试。

```sh
./build.sh
/opt/homebrew/bin/python3.13 -m pip install --only-binary=:all: --no-compile --target build/lib PyMuPDF==1.27.2.3 pypdf==6.21.4 Pillow==12.2.0
```

如已解压发布版，可以把上述 `--only-binary=:all:` 换成 `--no-index --find-links wheels`，从安装包内读取依赖。以下测试使用本机模拟设备。

```sh
export PYTHONPATH="src:tests:build/lib"
/opt/homebrew/bin/python3.13 tests/offline.py
/opt/homebrew/bin/python3.13 tests/cups_capture.py reference/sample.pdf /tmp/hp115nw-cups.json
```

要用自己的 Windows 文件重做双面对照，可给 `tests/compare_duplex.py` 依序传入随包样张、长边 4 页 600 dpi 的 PRN、短边 3 页 600 dpi 的 PRN 和输出 JSON 路径。Windows 对照文件使用 Letter、适合整张纸。用户提供的 PRN 包含作业元数据，只保存在本机原位置。

## 来源

程序通过独立的 CUPS/SPL3 编码器工作。SpliX 修改版以 GPL v2 分发，保留完整编码器源码及许可证。打印服务、控制界面及 Python 部分使用 AGPL v3 或更高版本。各依赖的许可证随源码或 wheel 分发。[OpenPrinting SpliX 对应版本](https://github.com/OpenPrinting/splix/tree/7a802180c7b50802aaa16bbd2b3e326237286730) · [goipp v1.2.0](https://github.com/OpenPrinting/goipp/tree/v1.2.0) · [PyMuPDF](https://github.com/pymupdf/PyMuPDF) · [pypdf](https://github.com/py-pdf/pypdf) · [Pillow](https://github.com/python-pillow/Pillow)

原厂配置、帮助和原始样张用于功能及格式对照。安装包仅包含本项目程序和开放源码依赖。[HP 111–117 原厂 Windows 驱动](https://support.hp.com/cn-zh/drivers/swdetails/hp-laser-mfp-100-printer-series/model/2102352114/swItemId/mp-338299-1?sku=A00F5A) · [HP 使用手册](https://kaas.hpcloud.hp.com/pdf-public/pdf_10580729_zh-CN-1.pdf)
