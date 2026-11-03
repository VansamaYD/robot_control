# 本地 MCP 接入

当前实现是真实stdio MCP服务，使用锁定的官方TypeScript SDK；它调用同一仿真后端，不连接实机，也不执行模型生成的任意Python或Shell。

0.14的恢复、重置和租约长按目前仅在本地UI开放，不新增MCP工具。UI重置会更换incarnation，模型需重新读取状态再截图；旧任务不会自动重启。

## 安装与启动

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci --ignore-scripts
npx playwright install chromium
.venv/bin/python output/prototype/v11/server.py --port 8765
```

另一个进程由MCP客户端启动：

```sh
node /ABSOLUTE/PATH/robot_control/output/prototype/v11/mcp_server.mjs
```

它等待JSON-RPC，不接受人工输入自然语言。日志使用stderr，stdout保留给协议。不要用会输出启动横幅的npm命令替代直接node命令。

参考`mcp.example.json`，将绝对路径替换为检出目录，并按客户端自身配置格式添加。示例采用常见mcpServers结构，并非承诺所有宿主使用相同配置格式；本轮实际验证的是官方SDK客户端。

WORKBENCH_URL默认`http://127.0.0.1:8765/`，只允许回环HTTP源。截图依赖已安装的Chrome或Playwright Chromium，可用CHROME_PATH指定Chrome位置。

GUI客户端若找不到node，可将command改为终端中`which node`得到的绝对路径；args中的脚本路径也必须替换为实际绝对路径。

## 已提供的12个工具

| 工具 | 内容 |
|---|---|
| get_capabilities | 当前仿真能力；hardware、vision、arbitrary_python为false |
| get_scene | 共享几何和机器人描述，不返回服务凭据 |
| get_robot_state | 最新状态，或指定incarnation与state_revision的历史状态 |
| list_programs | 已保存流程版本列表 |
| get_program_revision | 固定版本步骤及对应代码视图 |
| validate_program | 基于当前捕获起点检查运动学路径；不改变状态，不验证未来液量 |
| run_program | 按固定版本异步启动，返回job_id；必填幂等键 |
| get_job | 查询活动或已结束作业 |
| pause_job / resume_job / cancel_job | 操作本适配器拥有的指定作业 |
| render_snapshot | 返回真实PNG图像内容块及同帧元数据，可选default/top/side视角 |

工具返回结构化结果及文本；截图另含image/png内容块。参数错误或服务错误返回isError和可识别code。调用成功受理不等于作业完成，需要查询get_job。

## 建议调用流程

1. get_capabilities、list_programs，选择保存版本。
2. get_robot_state / get_scene读取上下文；可调用validate_program。其level为kinematic_path、liquid_validation=false，执行时仍重新检查路径和取汁条件。
3. run_program，提供program_revision及唯一idempotency_key。同一请求重试复用键；不同请求使用新键。
4. get_job查询进度；get_robot_state取得含incarnation、state_revision的完整帧。
5. render_snapshot传入该帧的两个标识；获得图像后继续判断。服务重启后旧incarnation返回SESSION_CHANGED，不能用同编号的新帧代替。
6. 需要时用指定job_id暂停、恢复或取消。

## 控制与截图边界

每个stdio适配器取得独立的本地能力令牌；令牌不返回模型。多个客户端可观察，但只有一个活动运动作业。客户端不能取消别的客户端任务；本地UI操作者可明确管理当前任务。旧任务的取消不能作用于新任务，任务UUID跨重启不复用。

服务最多同时运行两个截图工作进程。请求先复制并保留对应快照，用短期只读票据加载；渲染页拿不到UI控制令牌。捕获完成/失败后释放票据和资源。渲染不改变权威状态或操作者视角；尺寸和视角有限制，超时/无快照明确失败。

目前服务与作业历史在本机内存中，服务重启不恢复作业；适配器需要重新连接。身份隔离面向同一本地用户的客户端，不是公网或多租户权限体系。

## 自动验证

在服务启动、无活动任务时运行：

```sh
npm run test:mcp
npm run test:controls
```

第一条使用官方SDK进行真实握手、工具发现、任务/权限验证并接收PNG；第二条操作真实浏览器按钮，验证手动关节、遥测和截图预览。证据保存到`output/prototype/v11/evidence/`。
