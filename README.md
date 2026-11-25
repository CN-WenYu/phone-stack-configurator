# 手机堆叠支架配置器

## 在另一台电脑使用

解压 ZIP，双击 `index.html`，用支持 WebGL 2 的现代浏览器打开。预览所需的代码和模型数据都内嵌在页面里，断网也可以使用。

1. 选择取机方向、镂空样式和镂空率。
2. 输入单层外部长度、宽度和高度，单位为毫米。外部高度包含顶部卡扣。点击「应用尺寸」后查看内部净尺寸。
3. 点击「导出当前单层 STL」，将文件导入 Bambu Studio 切片。STL 以毫米为单位。

`models/` 提供三种默认款的 3MF 与 STL，对应六边形蜂窝和 50% 镂空。自定义尺寸由页面导出为 STL。3MF 文件包含模型几何，切片时按实际打印机、喷嘴和材料配置。

## 修改源码

- `src/preview.html` 包含页面结构、样式和预设网格数据。
- `src/preview.js` 包含三维交互、几何尺寸调整和 STL 导出。
- `src/phone_stack.py` 与 `src/stack_locks.py` 生成基础几何、预设网格和默认打印模型。
- `vendor/` 包含 Three.js 0.180.0、同版本 OrbitControls 的打包文件及 MIT 许可。

修改页面或交互后，在项目目录运行 `python build_preview.py` 重建 `index.html`。在 macOS 或 Linux 上可使用 `python3`，在 Windows 上可使用 `py`。

修改基础几何后，用 Python 3.10 或更新版本运行：

```sh
python -m pip install -r requirements.txt
python src/phone_stack.py --preview src/preview.html
python build_preview.py
```

模型生成命令会更新三种默认 3MF、STL 及预览的预设网格。可运行 `python src/phone_stack.py --help` 查看导出路径、镂空样式和镂空率参数。
