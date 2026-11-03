# Robot Control · 豉油鸡机械臂仿真工作台

纯仿真工作台：流程动作驱动SO-101五轴模型、汤勺和厨房场景，同步显示代码视图、关节和三维状态。支持流程版本保存/选择/导入导出、舀汁/倾倒/回流的体积近似，以及暂停、继续、固定步长、取消和历史快照渲染。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python output/prototype/v11/server.py --port 8765
```

需要Python 3.11+。打开 <http://127.0.0.1:8765/>。模型与Three.js已本地保存，启动后无需联网加载资源。服务仅监听本机，流程数据保存到被Git忽略的`data/projects/`。

```sh
.venv/bin/python -m unittest discover -s output/prototype/v11 -p 'test_*.py' -v
.venv/bin/python output/requirements/v07/check_requirements.py
```

可选浏览器测试（Node 20+，先启动服务；每轮完整测试前重启服务）：

```sh
npm ci --ignore-scripts
npx playwright install chromium
npm run test:browser
```

也可用`CHROME_PATH`指定已安装的Chrome。浏览器验证说明与边界见[原型文档](output/prototype/v11/README.md)，架构见[详细设计](output/design/v10/README.md)，版本变化见[CHANGELOG.md](CHANGELOG.md)。

当前增加了逐轴±1°有限手动移动、目标/当前角与模拟速度、页面截图预览和下载。手动移动经过路径检查，与自动作业互斥。MCP提供状态、保存流程执行、作业控制与真实PNG截图，接入和验证见[MCP.md](MCP.md)。截图及MCP需要先完成上述npm安装；仿真服务保持独立运行。

当前不连接真实机械臂或加热设备。步骤JSON经过校验后执行，代码视图由相同步骤生成；任意Python编辑/隔离执行尚未实现。液体是未标定近似，可编辑场景、翻鸡停靠与更完整的项目/调试能力仍需完善。

第三方模型与代码的来源、固定版本及许可见[THIRD_PARTY.md](THIRD_PARTY.md)。
