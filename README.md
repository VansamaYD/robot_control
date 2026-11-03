# Robot Control · 豉油鸡机械臂仿真工作台

纯仿真原型：可信流程代码驱动SO-101五轴模型、汤勺和厨房场景，同步显示关节、代码位置与三维状态。支持暂停、继续、固定步长、取消和指定历史快照渲染。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python output/prototype/v11/server.py --port 8765
```

打开 <http://127.0.0.1:8765/>。模型与Three.js已本地保存，启动后无需联网加载资源。服务仅监听本机。

```sh
.venv/bin/python -m unittest discover -s output/prototype/v11 -p 'test_*.py' -v
.venv/bin/python output/requirements/v07/check_requirements.py
```

浏览器验证说明、原型边界见[原型文档](output/prototype/v11/README.md)。架构与后续范围见[详细设计](output/design/v10/README.md)。浏览器验证脚本目前仍包含原开发环境路径，后续批次会移除该限制。

当前不连接真实机械臂或加热设备；不执行任意不可信Python代码。液体、代码管理和MCP按阶段完善，界面中未实现的能力不作为完成项。

第三方模型与代码的来源、固定版本及许可见[THIRD_PARTY.md](THIRD_PARTY.md)。
