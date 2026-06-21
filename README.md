# NBA Data Agent

一个面向 NBA 数据集的本地数据分析 Agent。项目包含 CSV 数据清洗入库脚本、只读 SQL 安全执行层、LangChain + DeepSeek 自然语言查询 Agent，以及 Flask 本地 Web 可视化界面。

## 功能特性

- 使用 pandas 清洗 CSV 数据并导入 MySQL。
- 支持 NBA 赛季、球队、球员、比赛、球队单场统计、球员赛季统计、奖项、选秀、联合试训等数据表。
- 通过 `schema_service.py` 限定 Agent 可访问的表、视图和字段说明。
- 通过 `sql_guard.py` 限制只读 SQL，仅允许 `SELECT` / `WITH` 查询白名单表和视图。
- 支持自然语言问题转 SQL 查询。
- Web 端支持多会话、会话删除、置顶、流式输出。
- 用户明确要求图表时，可生成本地 SVG 图表并渲染到会话窗口。
- 回答末尾自动追加本轮实际执行的关键 SQL。

## 目录结构

```text
NBA-data-agent/
├─ agent/                 # Agent、数据库连接、SQL 安全检查、工具和提示词
│  ├─ database.py          # MySQL 连接与只读 SQL 执行
│  ├─ schema_service.py    # 允许访问的表/视图/字段说明
│  ├─ sql_guard.py         # 只读 SQL 安全检查
│  ├─ tools.py             # LangChain 工具封装
│  ├─ chart_service.py     # SVG 图表生成
│  ├─ prompts.py           # NBA 数据分析系统提示词
│  └─ nba_agent.py         # DeepSeek 模型与 create_agent 入口
├─ app/                   # Flask 本地 Web UI
│  ├─ server.py            # Web API、SSE 流式输出、会话管理
│  ├─ templates/index.html
│  └─ static/
├─ sql/                   # pandas 清洗与入库脚本
├─ data/                  # 原始 CSV 数据
├─ outputs/charts/        # 生成的 SVG 图表
└─ requirements.txt
```

## 环境要求

- Python 3.10+
- MySQL 8.x
- DeepSeek API Key

安装依赖：

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

## 环境变量

在项目根目录创建 `.env`：

```env
DEEPSEEK_API_KEY=你的DeepSeek API Key
DEEPSEEK_BASE_URL=
NBA_AGENT_MODEL=deepseek-chat

NBA_DB_HOST=localhost
NBA_DB_PORT=3306
NBA_DB_USER=nba_agent
NBA_DB_PASSWORD=你的数据库密码
NBA_DB_NAME=nba
```

`DEEPSEEK_BASE_URL` 可留空。数据库用户建议只授予业务库所需权限；Web Agent 查询阶段建议使用只读账号。

## 数据库准备

项目假设 MySQL 中已创建业务表和视图。当前 Agent 白名单包含：

- 表：`seasons`、`teams`、`team_aliases`、`players`、`player_name_aliases`、`games`、`team_game_stats`、`period_scores`、`player_season_stats`、`season_awards`、`draft_records`、`draft_combine_measurements`
- 视图：`v_team_game_analysis`、`v_player_season_overall`、`v_player_team_season_stats`

这些表之间存在外键依赖，建议按下面顺序导入数据。

## 数据导入

所有导入脚本位于 `sql/`，支持 `--dry-run` 预览清洗结果，多数脚本支持 `--upsert` 更新已有记录。

推荐导入顺序：

```powershell
.\venv\Scripts\python.exe sql\load_data_to_seasons.py
.\venv\Scripts\python.exe sql\load_data_to_tams.py
.\venv\Scripts\python.exe sql\load_data_to_team_aliases.py
.\venv\Scripts\python.exe sql\load_data_to_players.py
.\venv\Scripts\python.exe sql\load_data_to_player_name_aliases.py
.\venv\Scripts\python.exe sql\load_data_to_games.py
.\venv\Scripts\python.exe sql\load_data_to_team_game_stats.py
.\venv\Scripts\python.exe sql\load_data_to_period_scores.py
.\venv\Scripts\python.exe sql\load_data_to_player_season_stats.py
.\venv\Scripts\python.exe sql\load_data_to_season_awards.py
.\venv\Scripts\python.exe sql\load_data_to_draft_records.py
.\venv\Scripts\python.exe sql\load_data_to_draft_combine_measurements.py
```

预览示例：

```powershell
.\venv\Scripts\python.exe sql\load_data_to_seasons.py --dry-run
```

更新已有记录示例：

```powershell
.\venv\Scripts\python.exe sql\load_data_to_players.py --upsert
```

## 命令行使用

直接询问自然语言问题：

```powershell
.\venv\Scripts\python.exe agent\nba_agent.py "2022-2023常规赛场均得分最高的球员是谁"
```

关闭流式输出：

```powershell
.\venv\Scripts\python.exe agent\nba_agent.py "库里的生涯三分数" --no-stream
```

## Web 界面

启动 Flask：

```powershell
.\venv\Scripts\flask.exe --app app.server run --host 127.0.0.1 --port 5000
```

浏览器打开：

```text
http://127.0.0.1:5000/
```

Web 端能力：

- 左侧会话管理：新建、删除、置顶、切换会话。
- 中间问答窗口：支持流式输出。
- 右侧图表区域：展示当前会话生成的图表。
- 输入框：`Enter` 发送，`Shift + Enter` 换行。
- 输出过程中可自由滚动；只有停留在底部附近时才自动跟随最新输出。

图表只在用户明确要求时生成，例如：

```text
画出2022-2023常规赛场均得分前10球员的柱状图
```

普通统计/排名/分析问题不会自动生成图表。

## Docker 运行

项目提供单容器运行方式：容器内启动 MariaDB，首次启动时创建 NBA 数据库、导入 `data/` 下的 CSV 数据，然后启动 Flask Web 服务。

构建镜像：

```powershell
docker build -f DockerFIle -t nba-data-agent .
```

启动容器：

```powershell
docker run --name nba-data-agent `
  -e API_KEY=你的DeepSeek_API_Key `
  -p 5000:5000 `
  nba-data-agent
```

浏览器打开：

```text
http://127.0.0.1:5000/
```

指定本机端口，例如映射到 `8080`：

```powershell
docker run --name nba-data-agent `
  -e API_KEY=你的DeepSeek_API_Key `
  -p 8080:5000 `
  nba-data-agent
```

然后访问：

```text
http://127.0.0.1:8080/
```

为了保留容器内 MySQL 数据，建议挂载 volume：

```powershell
docker volume create nba_mysql_data

docker run --name nba-data-agent `
  -e API_KEY=你的DeepSeek_API_Key `
  -p 5000:5000 `
  -v nba_mysql_data:/var/lib/mysql `
  nba-data-agent
```

可选环境变量：

```text
API_KEY                  DeepSeek API Key，等价于 DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL         DeepSeek 兼容接口地址，可留空
NBA_AGENT_MODEL           默认 deepseek-chat
NBA_DB_NAME               默认 nba
NBA_DB_USER               默认 nba_agent
NBA_DB_PASSWORD           默认 nba_agent
```

注意：首次启动需要初始化 MySQL 并导入 CSV，耗时会比普通启动长。导入完成后会在 MySQL 数据目录写入标记文件，后续启动会跳过导入。

## Agent 查询安全

Agent 工具链包含：

- `get_nba_schema`：返回允许访问的表、视图和字段说明。
- `validate_sql`：检查 SQL 是否为安全只读查询。
- `execute_sql`：执行白名单内的只读 SQL。
- `generate_chart`：在用户明确要求图表时生成 SVG 图表。

安全限制：

- 禁止访问 `information_schema`、`mysql`、`performance_schema` 等系统库。
- 禁止 `INSERT`、`UPDATE`、`DELETE`、DDL 等写操作。
- 默认查询返回最多 200 行，工具层上限 500 行。

## 图表输出

图表由 `agent/chart_service.py` 使用标准库生成 SVG，不依赖 matplotlib。生成路径：

```text
outputs/charts/
```

支持类型：

- `bar`：柱状图
- `line`：折线图
- `pie`：饼图

## 常见问题

### Web 端没有使用 prompts.py 吗？

使用了。调用链为：

```text
app/server.py -> agent/nba_agent.py build_agent() -> create_agent(system_prompt=NBA_ANALYST_SYSTEM_PROMPT)
```

### 为什么有些问题没有关键 SQL？

回答末尾的关键 SQL 来自本轮真实 `execute_sql` 工具调用。若模型未调用数据库工具，系统不会追加空 SQL 提示。

### 为什么“生涯三分数”等结果是估算？

当前库没有逐场球员投篮明细。Agent 会使用 `player_season_stats` 中：

```sql
SUM(three_points_per_game * games_played)
```

估算赛季或生涯三分命中数，并在回答中说明口径。

## 开发检查

语法检查：

```powershell
.\venv\Scripts\python.exe -m py_compile agent\nba_agent.py app\server.py
node --check app\static\app.js
```

## 注意事项

- `.env`、`venv/`、`outputs/` 已在 `.gitignore` 中忽略。
- `data/` 目录包含原始 CSV 数据，体积较大时可按需管理。
- 若修改了后端或提示词，需重启 Flask 服务后生效。
