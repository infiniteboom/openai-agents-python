好的，根据你的描述，我将你的需求整理成了一份**详细的技术方案规格说明书（Technical Specification）**。

这份方案将模糊的自然语言需求转化为严谨的代码逻辑，明确了输入、输出、业务规则以及 Agent 的工具调用策略。

---

### 1. 核心目标 (Core Objective)

构建一个**金融询价解析 Agent**，接收交易员非标准化的自然语言文本（询价），结合**当前时间上下文**，输出符合下游定价系统要求的**标准化 JSON 数据**。

* **约束：** 当前版本不支持追问/澄清（no human-in-the-loop）。当关键信息缺失或歧义无法消除时，只能做确定性推断或输出缺失标记，交由后处理拦截或补全。

---

### 2. 数据协议 (Data Protocol)

#### 2.1 输入 (Input)

* **Text:** 用户询价文本（例如：“hc10合约，我方卖一个月平值期权”）
* **Context:**
* `current_date`: 当前日期（用于计算到期日）
* `current_year`: 当前年份（用于推断合约年份）



#### 2.2 输出 (Output JSON Schema)

| 字段名 | 类型 | 值域 | 说明 |
| --- | --- | --- | --- |
| `contract_code` | `str \| null` | `"HC2610"` | **逻辑组合字段**：品种代码(大写) + 年份(2位) + 月份(2位)。若文本未提供且无法推断则为 `null`（由后处理拦截或补全）。 |
| `call_put` | `int \| null` | {`1`,`2`, `null`} | **权利方**：`1`=看涨(Call), `2`=看跌(Put)。若文本未提供且无法推断则为 `null`（由后处理拦截或补全）。 |
| `buy_sell` | `int \| null` | {`-1`,`1`, `null`} | **客户方向**：`1`=客户买入, `-1`=客户卖出 (需进行视角转换)。若文本歧义无法消除则为 `null`（不追问）。 |
| `strike` | `float \| null` | 大于0 | **绝对行权价**：与 `strike_offset` 互斥，二选一填入，另一个为 `null` |
| `strike_offset` | `float \| null` | 不限 | **相对行权价**：平值=0，虚/实值需定义正负号逻辑 |
| `underlying_price` | `float \| null` | 不限 | **入场参考价**：可选，未指定则为 `null` |
| `expire_date` | `str \| null` | `"2026-03-12"` | **到期日期**：格式 `YYYY-MM-DD`，由直接提取或工具计算得出。若文本未提供且无法推断则为 `null`（由后处理拦截或补全）。 |

---

### 3. 关键业务逻辑 (Business Logic)

#### A. 合约代码推断 (Contract Code Inference)

* **规则：** 用户通常只说月份（如 "hc10"），Agent 需补全年份。
* **逻辑：**
* 提取品种代码（如 `hc` -> `HC`）。
* 提取月份（如 `10`）。
* **年份判断：**
* 若 `目标月份 >= 当前月份`：年份 = `当前年份` (例如当前2月，目标10月 -> 2610)。
* 若 `目标月份 < 当前月份`：年份 = `当前年份 + 1` (例如当前11月，目标1月 -> 2701)。





#### B. 买卖方向转换 (Perspective Flip)

* **规则：** 系统字段定义为**“客户方向”**。
* **逻辑：**
* 若文本含 “客户买”、“买入” -> `1`。
* 若文本含 “客户卖”、“卖出” -> `-1`。
* 若文本含 “我方卖”、“我们卖”、“卖给你”、“offer” -> `1`（客户买入）。
* 若文本含 “我方买”、“我们买”、“从你买”、“bid” -> `-1`（客户卖出）。
* **冲突处理：** 若出现同时指向买/卖的关键词且无法消歧，则输出 `null`（不追问）。



#### C. 行权价互斥与符号 (Strike Logic)

* **规则：** `strike` 和 `strike_offset` 只能存在一个。
* **冲突处理：** 若同时出现 `strike` 和 `strike_offset`，优先保留 `strike`，并将 `strike_offset` 置为 `null`。
* **Offset 符号定义（需确认）：**
* 采用 **Moneyness (价内/价外)** 逻辑：
* 平值 (ATM) = `0`
* 实值 (ITM) / "实30" = `+30`
* 虚值 (OTM) / "虚30" = `-30`


#### C2. 看涨/看跌提取 (Call/Put Logic)

* **规则：** 看涨填 `1`，看跌填 `2`。
* **关键词示例：**
* 看涨/认购/Call/C -> `1`
* 看跌/认沽/Put/P -> `2`
* **缺失处理：** 若未出现任何可识别关键词且无法从上下文确定，则输出 `null`（不进行追问，不猜测）。


#### D. 到期日计算 (Expiration Logic)

* **绝对日期：** 用户说 "4月15到期" -> 结合当前年份，处理跨年逻辑 -> 输出 "2026-04-15"。
* **冲突处理：** 若同时出现绝对日期和相对期限，优先绝对日期。
* **相对日期（必须调用工具）：**
* "1个月" -> 调用 `get_expire_date_by_months(1)`。
* "20天" / "20日" -> 调用 `get_expire_date_by_natural_date(20)`。
* "20个交易日" -> 调用 `get_expire_date_by_trading_date(20)`。



---

### 4. Agent 架构与工具 (System Architecture)

我们将使用 **Tool-Calling Agent** 模式。

#### 4.1 涉及的工具 (Function Tools)

1. **`get_expire_date_by_months(months: float) -> str`**
* **场景：** 用户提到“月”为单位的期限。
* **功能：** 基于当前系统日期，向后推算自然月。支持小数（如 0.5个月）。


2. **`get_expire_date_by_natural_date(days: int) -> str`**
* **场景：** 用户提到“天”、“日”为单位的期限（自然日）。
* **功能：** 基于当前系统日期，向后推算自然日（不跳过周末/节假日）。


3. **`get_expire_date_by_trading_date(days: int) -> str`**
* **场景：** 用户提到“交易日”为单位的期限（如“20个交易日”）。
* **功能：** 基于当前系统日期，向后推算交易日（跳过周末/节假日逻辑，节假日历来源需在实现中明确）。



#### 4.2 处理流程 (Pipeline)

1. **用户输入** ->
2. **预处理** (注入当前日期 Context) ->
3. **LLM 分析** (提取品种、方向、价格，判断期限类型) ->
4. **决策分支**：
* *分支A (含相对日期)* -> **调用 Tool** -> 获得日期 -> **生成 JSON**。
* *分支B (含绝对日期)* -> **直接计算** -> **生成 JSON**。


5. **后处理校验** (确保 `buy_sell` 是 int，`contract_code` 格式正确)。
* 若下游定价系统要求字段必填（例如 `call_put`、`expire_date`），建议在后处理阶段对 `null` 做硬拦截，避免错误下游定价。

---


---

## 2026-02-13 ExecPlan Addendum

# 迁移 financial_inquiry_parser 到 Agno（仅该示例）并接入 AG-UI 调试页

This ExecPlan is a living document. The sections Progress, Surprises & Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as work proceeds.

If `PLANS.md` is present in the repo, maintain this document in accordance with it and link back to it by path (`PLANS.md`).

## Purpose / Big Picture
目标是在不影响仓库其他 examples 的前提下，把 `examples/financial_inquiry_parser` 的运行层从 OpenAI Agents SDK 迁到 Agno，并提供一个可视化网页调试入口（AG-UI），用于观察多腿拆分、候选品种、工具调用与最终结构化结果。用户可直接在网页里输入 RFQ 并看到每条腿的解析结果。

## Progress
- [x] (2026-02-13) 完成 ExecPlan 草案，等待你确认实施。
- [ ] 建立迁移分支与最小可运行骨架（CLI 先通）。
- [ ] 完成 Agno 版 `financial_inquiry_parser` 后端。
- [ ] 完成 AG-UI 对接（仅该示例）。
- [ ] 建立回归测试集与自动化回归脚本。
- [ ] 对比验收并收敛差异。

## Surprises & Discoveries
- 观察：当前仓库全量测试已有与本任务无关失败（codex 扩展测试），不适合用作本任务唯一验收门槛。
  Evidence: `tests/extensions/experiemental/codex/test_codex_tool.py` 出现 `.FF`。

## Decision Log
- Decision: 仅迁移 `examples/financial_inquiry_parser`，其他示例保持 OpenAI Agents SDK 不动。
  Rationale: 控制风险，快速验证 AG-UI 价值。
  Date/Author: 2026-02-13 / Codex
- Decision: 业务解析逻辑（`normalize.py`、`schema.py`、生成脚本）尽量复用，不重写。
  Rationale: 保持行为一致，减少回归成本。
  Date/Author: 2026-02-13 / Codex

## Outcomes & Retrospective
待实施后回填：产出、差异、遗留问题、后续建议。

## Context and Orientation
当前关键文件：
- `examples/financial_inquiry_parser/normalize.py`：工具与归一化逻辑（含多腿支持、`quantity`）。
- `examples/financial_inquiry_parser/schema.py`：`InquiryQuote` 契约。
- `examples/financial_inquiry_parser/run_tool_based.py`：当前 OpenAI Agents SDK 入口。
- `tests/test_financial_inquiry_parser_normalize.py`：核心单元测试。

术语说明：
- “运行层迁移”= 只替换 Agent 编排与调用框架，不改业务字段含义。
- “AG-UI 调试页”= 前端展示消息流与工具调用轨迹，不负责最终生产报价逻辑。

## Plan of Work
第一阶段先做 CLI 等价迁移：新增 Agno 版 runner（平行文件），复用现有工具函数与 schema，确保单腿/多腿输出契约一致。第二阶段接 AG-UI：将 Agno 运行事件映射到 AG-UI 前端所需事件流。第三阶段补回归：构建示例集（单腿、多腿、中文别名、代码缩写、quantity、期限表达）并形成一键回归命令。最后做行为对比与文档。

## Concrete Steps
1. 新增 Agno 版入口（不覆盖旧文件）：
   - `examples/financial_inquiry_parser/run_tool_based_agno.py`
2. 新增 AG-UI 后端桥接与最小页面：
   - `examples/financial_inquiry_parser/web/`（后端 + 前端）
3. 新增回归数据与测试：
   - `examples/financial_inquiry_parser/testdata/*.json`
   - `tests/test_financial_inquiry_parser_regression.py`
4. 验证命令（仓库根目录）：
   - `uv run python -m examples.financial_inquiry_parser.run_tool_based_agno --current-date 2026-02-13 "<RFQ>"`
   - `uv run pytest -q tests/test_financial_inquiry_parser_normalize.py tests/test_financial_inquiry_parser_regression.py`
   - `make format && make lint && make mypy`

## Validation and Acceptance
验收标准：
- 单腿输入：输出字段与当前实现语义一致（含 `quantity`）。
- 多腿输入：返回列表，每腿都有独立 `InquiryQuote`。
- 不确定品种时：可调用候选能力；确定时不强制调用。
- AG-UI 页面可实时看到：输入、候选调用、定价调用、最终结果。
- 回归集通过率达到目标阈值（先设 100% 针对收录样例）。

## Idempotence and Recovery
- 采用平行新增文件，不覆盖旧入口，支持随时回退到 `run_tool_based.py`。
- 每步可独立运行验证；失败只回滚新增文件。
- 保持旧 CLI 可继续使用，直到你确认切换默认入口。

## Artifacts and Notes
- 后续会补：
  - 迁移前后同一批 RFQ 的对比输出样例。
  - AG-UI 页面截图与事件日志片段。
  - 回归样例清单与失败分析。

## Interfaces and Dependencies
最终应存在：
- `run_tool_based_agno.py`（Agno 运行入口）
- `InquiryQuote` 契约不破坏现有字段语义（新增字段继续尾部扩展原则）
- AG-UI 对接层（仅服务 `financial_inquiry_parser`）
- 回归测试集与测试文件

新增依赖风险（需你确认）：
- 若引入 Agno/AG-UI 相关包，会变更 `pyproject.toml` 与 `uv.lock`，属于用户可见运行环境变化。
