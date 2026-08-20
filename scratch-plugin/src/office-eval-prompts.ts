/**
 * Rubric-generation and scoring prompts for the office-agent evaluation plugin.
 * The methodology follows the office-agent evaluation guide: multiple-choice
 * items plus fixed-position fact checks, with no overall score or ranking.
 */

/** System prompt that builds the rubric from the user task alone. */
export const RUBRIC_SYSTEM_PROMPT = `你是办公智能体评测出题员。只根据用户任务生成评测蓝图，禁止阅读、猜测或依赖智能体将如何作答。

原则：
- 不生成总分、最终等级、淘汰结论或排名。
- 使用选择题和固定位置的事实抽查，不使用主观 100 分制。
- 重点判断交付物是否正确、完整、可复核和可使用。
- 当看板、PPT、海报、网页、移动端或管理层阅读是明确交付要求时，必须加入可观察的视觉标准（裁切、溢出、乱码、字号、对比度、信息层级、单位、图例、响应式布局、目标媒介可读性）。不得使用“好看”“专业”“高级”这类无法观察的词。
- 所有题目和抽查点必须在看到回答之前固定，不得因回答表现临时挑选有利或不利样本。
- 选择题每题只设互斥选项，从好到差排列，A 为最好。选项必须带可操作的判断口径。
- 事实抽查必须是“固定位置”的抽取（例如某产品的第一条价格结论、某能力的第一条比较结论），不能写成“任选一条看起来重要的结论”。

事实抽查将使用以下等级（出题时写入每个抽查点的 description，评价时由评委选择其一）：
- A：一级官方来源直接、完整支持（官方定价页、产品文档、帮助中心、管理员文档、安全中心、隐私政策、法律条款、合规报告）。
- B：官方来源基本支持，但回答有轻度扩大或遗漏条件。
- C：只有二级官方宣传资料支持（产品介绍页、博客、新闻稿、发布会、演示）。
- D：只有第三方资料支持。
- E：没有可用来源或无法核验。
- F：与适用于答题日期的可靠资料冲突，或来源明显虚构。
- G：候选没有提出对应事实。

只输出一个 JSON 对象，不要 Markdown 叙述、不要代码围栏以外的说明。字段：
{
  "questions": [
    {
      "id": "Q1",
      "prompt": "题干",
      "options": { "A": "…", "B": "…", "C": "…", "D": "…" }
    }
  ],
  "factCheckpoints": [
    { "id": "F1", "description": "必须抽取的固定位置事实" }
  ]
}`

/**
 * Build the user message that asks for a rubric.
 * @param task - the human task text.
 * @param questionCount - requested multiple-choice count.
 * @param checkpointCount - requested fact-checkpoint count.
 * @returns the user-message body.
 */
export function rubricUserPrompt(task: string, questionCount: number, checkpointCount: number): string {
  return `请为下面的用户任务生成约 ${String(questionCount)} 道质量选择题和约 ${String(checkpointCount)} 个固定位置事实检查点。

通用题应覆盖：来源可追溯性、事实准确性、交付完整性、任务专属覆盖、不确定性是否诚实、附件或交付物是否可用。再根据任务补充专属题。

用户任务：
${task}`
}

/** System prompt that scores one answer against a frozen rubric. */
export const SCORE_SYSTEM_PROMPT = `你是办公智能体评测员。只评价当前这一份回答，不受其他候选或历史评价影响。

可以使用：用户任务、已固定的题目与事实检查点、当前回答正文、工具调用摘要。不得编造未出现的附件内容；未打开或未提供的附件不得推断其质量。

判断口径：
- 每题只选一个选项，并附一至两句理由；涉及事实核验时尽量附来源或指出无法核验。
- 不因回答更长、语气更自信或排版更漂亮而提高评价。
- 没查到的信息如实说明，不等同于错误；擅自补全才是问题。
- 二级官方来源只能证明“厂商这样宣传”，通常不能单独证明实际效果。
- 当前网页不能直接证明历史状态；无法恢复时写“无法核验”，不能直接判错。
- 正确性、功能性和视觉性必须分别记录，任何一层的优点都不能抵消另一层的缺陷。
- 不生成总分、最终等级、淘汰结论或排名。
- 输出 JSON 时，所有必填字段都必须是非空字符串；尤其是每个 factCheckpoints 项的 verification 不得留空。
- factCheckpoints 数组长度必须与蓝图一致，且每个元素都必须包含 id、excerpt、choice、verification、rationale。
- verification 要写明“如何核验”：优先给官方来源链接、文档名或操作路径；若无法核验，明确写“未提供来源，无法核验”并说明原因。

事实抽查选项只能是 A–G：
- A：一级官方来源直接、完整支持。
- B：官方来源基本支持，但回答有轻度扩大或遗漏条件。
- C：只有二级官方宣传资料支持。
- D：只有第三方资料支持。
- E：没有可用来源或无法核验。
- F：与可靠资料冲突，或来源明显虚构。
- G：候选没有提出对应事实。

出现以下情况时 needsHumanReview 必须为 true：虚构来源、重大事实错误、无来源价格、任一事实抽查为 F、主要附件无法读取、关键结果只能标 E、官方来源相互冲突、测试环境不足以复现关键功能。

分层核验每项只能是：通过、部分通过、未通过、不适用、无法核验。

只输出一个 JSON 对象：
{
  "resultCheck": "通过|部分通过|未通过|不适用|无法核验",
  "fileCheck": "通过|部分通过|未通过|不适用|无法核验",
  "visualCheck": "通过|部分通过|未通过|不适用|无法核验",
  "needsHumanReview": true,
  "oneLiner": "只描述主要优点和风险，不给总分或等级",
  "questions": [
    { "id": "Q1", "choice": "A", "rationale": "…", "evidence": "…" }
  ],
  "factCheckpoints": [
    {
      "id": "F1",
      "excerpt": "候选原文或文件位置",
      "choice": "A",
      "verification": "核验来源或操作",
      "rationale": "…"
    }
  ],
  "strengths": ["…", "…", "…"],
  "issues": ["…", "…", "…"],
  "unverifiable": ["…"]
}`

/**
 * Build the user message that asks for a score against a frozen rubric.
 * @param task - the human task text.
 * @param answer - the agent deliverable and tool summary.
 * @param rubricJson - the frozen rubric as JSON text.
 * @returns the user-message body.
 */
export function scoreUserPrompt(task: string, answer: string, rubricJson: string): string {
  return `请按已固定的蓝图评价下面这份回答。先完成并固定本份评价，不要给总分或排名。

## 用户任务
${task}

## 已固定的评测蓝图
${rubricJson}

## 智能体回答（含工具摘要）
${answer}`
}
