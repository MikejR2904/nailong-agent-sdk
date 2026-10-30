// Builds nailong-architecture.pptx. Run:
//   NODE_PATH=<dir with node_modules/pptxgenjs> node build_deck.js <skill dir> <out.pptx>
const pptxgen = require("pptxgenjs");
const path = require("path");

const SKILL = process.argv[2];
const OUT = process.argv[3] || "nailong-architecture.pptx";
const { applyTheme } = require(path.join(SKILL, "scripts", "apply_theme.js"));

const THEME = {
  name: "Nailong Ledger",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "12262B", lt1: "FFFFFF", dk2: "1F4E55", lt2: "EDF3F2",
    accent1: "1F7A72", accent2: "D9822B", accent3: "6B7F86",
    accent4: "B5483A", accent5: "3E6FA3", accent6: "8AA68F",
    hlink: "1F7A72", folHlink: "6B7F86",
  },
};

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "Agent memory you can debug";
pres.author = "David Michael Indraputra";
const C = pres.SchemeColor;
const SH = pres.ShapeType;

pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: C.background1 },
  margin: [0.5, 0.6, 0.5, 0.6],
  objects: [
    {
      placeholder: {
        options: { name: "title", type: "title", x: 0.6, y: 0.35, w: 12.1, h: 1.0, fontSize: 30, bold: true, color: C.text1, valign: "top", align: "left", margin: 0 },
        text: "",
      },
    },
  ],
  slideNumber: { x: 12.3, y: 7.05, w: 0.5, h: 0.3, fontSize: 10, color: C.accent3 },
});
pres.defineSlideMaster({
  title: "DARK",
  background: { color: C.text1 },
  objects: [
    {
      placeholder: {
        options: { name: "title", type: "title", x: 0.9, y: 2.2, w: 11.4, h: 1.9, fontSize: 46, bold: true, color: C.background1, valign: "top", align: "left", margin: 0 },
        text: "",
      },
    },
  ],
});

function T(s, text, o) {
  s.addText(text, Object.assign({ isTextBox: true, margin: 0, valign: "top", color: C.text1, fontSize: 14 }, o));
}
function box(s, text, x, y, w, h, o = {}) {
  s.addText(text, Object.assign({
    shape: SH.roundRect, rectRadius: 0.06, x, y, w, h,
    fill: { color: C.background2 }, line: { color: C.accent3, width: 1 },
    align: "center", valign: "middle", fontSize: 13, color: C.text1, margin: 0.06,
  }, o));
}
function arrow(s, x1, y1, x2, y2, o = {}) {
  s.addShape(SH.line, {
    x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1),
    flipH: x2 < x1, flipV: y2 < y1,
    line: { color: o.color || C.accent3, width: o.width || 1.75, endArrowType: o.noHead ? "none" : "triangle", dashType: o.dash || "solid" },
  });
}
function slide(title, notes) {
  const s = pres.addSlide({ masterName: "CONTENT" });
  s.addText(title, { placeholder: "title" });
  s.addNotes(notes);
  return s;
}
function bullets(items, o = {}) {
  return items.map((t, i) => ({
    text: t,
    options: { bullet: true, breakLine: i < items.length - 1, paraSpaceAfter: o.gap || 6 },
  }));
}
const TEAL = { color: C.accent1 }, AMBER = { color: C.accent2 }, BRICK = { color: C.accent4 };

// 1. Title -------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "DARK" });
  s.addText("Agent memory you can debug", { placeholder: "title" });
  T(s, "Typed episodes, exact compaction and verified summaries in nailong-agent-sdk", { x: 0.9, y: 4.2, w: 10.5, h: 0.9, fontSize: 22, color: C.background2 });
  T(s, "David Michael Indraputra  |  PhD discussion draft  |  October 2026", { x: 0.9, y: 6.3, w: 10, h: 0.4, fontSize: 14, color: C.accent6 });
  s.addNotes("Frame the talk as a systems-and-method contribution about long-running tool-using agents. The claim is about structure and checkability of memory, not about a new model. Everything shown was checked against branch sdk_fix at commit 5812431.");
}

// 2. Requirements ---------------------------------------------------------
{
  const s = slide("Three requirements drive every design choice",
    "State the requirements before any architecture. R1 is the one everyone knows. R2 and R3 are where plain text memory fails. Each later slide ends by pointing back to one of these.");
  const cols = [
    ["R1  Bounded", "The context window is fixed. A 200-step run is not.", "Choose what to keep under a token budget, deterministically.", C.accent1],
    ["R2  Recoverable", "A detail that was dropped must be fetchable, not just forgotten.", "Full results live outside the context; summaries point back to them.", C.accent5],
    ["R3  Diagnosable", "When the final answer is wrong, we must name the layer that failed.", "Typed stores, verification records and hash-chained ledgers.", C.accent2],
  ];
  cols.forEach((c, i) => {
    const x = 0.6 + i * 4.15;
    s.addShape(SH.roundRect, { x, y: 1.7, w: 3.9, h: 4.3, rectRadius: 0.08, fill: { color: C.background2 }, line: { color: C.background2, width: 0 } });
    s.addShape(SH.ellipse, { x: x + 0.3, y: 1.95, w: 0.7, h: 0.7, fill: { color: c[3] }, line: { color: c[3], width: 0 } });
    T(s, c[0].slice(0, 2), { x: x + 0.3, y: 1.95, w: 0.7, h: 0.7, align: "center", valign: "middle", color: C.background1, bold: true, fontSize: 16 });
    T(s, c[0].slice(4), { x: x + 1.15, y: 2.0, w: 2.6, h: 0.6, fontSize: 22, bold: true, valign: "middle", fontFace: THEME.headFontFace });
    T(s, c[1], { x: x + 0.3, y: 3.0, w: 3.3, h: 1.2, fontSize: 16 });
    T(s, "Mechanism", { x: x + 0.3, y: 4.35, w: 3.3, h: 0.3, fontSize: 12, bold: true, color: C.accent3 });
    T(s, c[2], { x: x + 0.3, y: 4.7, w: 3.3, h: 1.1, fontSize: 14 });
  });
  T(s, "Bounded alone is what rolling summarisation already gives. The contribution is getting R2 and R3 without giving up R1.", { x: 0.6, y: 6.3, w: 12.1, h: 0.6, fontSize: 16, italic: true });
}

// 3. Agent architecture -----------------------------------------------------
{
  const s = slide("Agent architecture: the model proposes, deterministic code decides",
    "Key sentence: the model is an untrusted proposal source. In the common ReAct-style loop the message list is the state, and permissions, memory and logging are conveniences around it. Here the harness owns the state and the model emits exactly one of four turn types per iteration. This is what makes R3 possible: a wrong answer can be attributed to the model, a tool, the selection or the summary because they are separate components. Layers in the figure are the real folder-level import order.");
  // left: layers
  box(s, "Host backend  (owns sessions, approvals, roles)", 0.6, 1.65, 6.2, 0.6, { fill: { color: C.background1 }, line: { color: C.accent3, width: 1 } });
  box(s, "MCP service / AgentTaskRunner  (credentials, options validated as a whole)", 0.6, 2.5, 6.2, 0.6, { fill: { color: C.background1 } });
  s.addShape(SH.roundRect, { x: 0.6, y: 3.4, w: 6.2, h: 3.25, rectRadius: 0.06, fill: { color: C.background2 }, line: { color: C.accent1, width: 2 } });
  T(s, "Harness: BaseAgent loop", { x: 0.8, y: 3.5, w: 5.8, h: 0.35, fontSize: 15, bold: true, color: C.accent1 });
  box(s, "Context projection\nmemory/", 0.8, 4.0, 1.8, 0.95, { fill: { color: C.background1 }, line: { color: C.accent2, width: 2 } });
  box(s, "Capability policy\n+ approvals", 2.75, 4.0, 1.8, 0.95, { fill: { color: C.background1 } });
  box(s, "Verification\ngate", 4.7, 4.0, 1.9, 0.95, { fill: { color: C.background1 } });
  box(s, "Project state\n(typed transitions)", 0.8, 5.15, 1.8, 0.95, { fill: { color: C.background1 } });
  box(s, "Tool executors\nprocess, sandbox, MCP", 2.75, 5.15, 1.8, 0.95, { fill: { color: C.background1 } });
  box(s, "Ledgers: telemetry,\naudit, profile", 4.7, 5.15, 1.9, 0.95, { fill: { color: C.background1 } });
  arrow(s, 3.7, 2.25, 3.7, 2.5);
  arrow(s, 3.7, 3.1, 3.7, 3.4);
  // model
  box(s, "LLM provider\nuntrusted proposal source", 8.0, 4.2, 2.3, 1.1, { fill: { color: C.accent4 }, line: { color: C.accent4, width: 1 }, color: C.background1, bold: true });
  arrow(s, 6.8, 4.55, 8.0, 4.55, { color: C.accent4 });
  arrow(s, 8.0, 4.95, 6.8, 4.95, { color: C.accent4 });
  T(s, "context out", { x: 6.85, y: 4.3, w: 1.1, h: 0.25, fontSize: 11, color: C.accent3, align: "center" });
  T(s, "one turn back", { x: 6.85, y: 5.0, w: 1.1, h: 0.25, fontSize: 11, color: C.accent3, align: "center" });
  // right column text
  T(s, "State of the art", { x: 8.0, y: 1.65, w: 4.7, h: 0.3, fontSize: 14, bold: true, color: C.accent3 });
  T(s, "The message list is the state; permissions, memory and logging are framework conveniences around it.", { x: 8.0, y: 2.0, w: 4.7, h: 0.9, fontSize: 13 });
  T(s, "Four turn types only", { x: 10.55, y: 4.2, w: 2.2, h: 0.3, fontSize: 14, bold: true, color: C.accent1 });
  T(s, "final, blocked, tool-call, tool-batch. Anything else ends the run FAILED, naming up to five bad fields.", { x: 10.55, y: 4.55, w: 2.2, h: 1.6, fontSize: 12 });
  T(s, "Why we differ", { x: 8.0, y: 5.6, w: 4.8, h: 0.3, fontSize: 14, bold: true, color: C.accent2 });
  T(s, "Separate components make a failure attributable (R3).", { x: 8.0, y: 5.95, w: 4.8, h: 0.7, fontSize: 13 });
}

// 4. One run ------------------------------------------------------------------
{
  const s = slide("One run is a fixed loop with one nondeterministic step",
    "Walk left to right. Everything except the model call is deterministic and typed. The three guards fire before the model is called, so a context that cannot fit never reaches the provider. The single exit _terminate means every outcome, including failures, writes the same evidence. Failure codes named on the slide are real: CONTEXT_DEADLOCK, PROJECT_STATE_BUDGET_EXCEEDED, CONTEXT_BUDGET_EXCEEDED, MODEL_TURN_INVALID.");
  const y = 1.7, h = 1.05, w = 2.2;
  const xs = [0.6, 3.25, 5.9, 8.55, 11.2];
  const labels = [
    ["1  Project context", "compact, stub,\nsummarise", C.accent2],
    ["2  Guards", "deadlock, state budget,\ncontext budget", C.accent1],
    ["3  Model call", "watchdog, retry\non 429 / 5xx", C.accent4],
    ["4  Validate turn", "shape and fields", C.accent1],
    ["5  Act", "tool batch, final,\nor blocked", C.accent1],
  ];
  labels.forEach((l, i) => {
    box(s, "", xs[i], y, i === 4 ? 1.55 : w, h, { fill: { color: l[2] }, line: { color: l[2], width: 0 } });
    T(s, l[0], { x: xs[i] + 0.1, y: y + 0.08, w: (i === 4 ? 1.55 : w) - 0.2, h: 0.35, fontSize: 14, bold: true, color: C.background1 });
    T(s, l[1], { x: xs[i] + 0.1, y: y + 0.45, w: (i === 4 ? 1.55 : w) - 0.2, h: 0.55, fontSize: 12, color: C.background1 });
    if (i < 4) arrow(s, xs[i] + w, y + h / 2, xs[i + 1], y + h / 2);
  });
  // loop back
  arrow(s, 11.97, y + h, 11.97, 3.4, { color: C.accent3, noHead: true, dash: "dash" });
  arrow(s, 11.97, 3.4, 1.7, 3.4, { color: C.accent3, noHead: true, dash: "dash" });
  arrow(s, 1.7, 3.4, 1.7, y + h, { color: C.accent3, dash: "dash" });
  T(s, "tool results become observations, episodes and state transitions, then the next iteration", { x: 3.2, y: 3.5, w: 6.8, h: 0.3, fontSize: 12, color: C.accent3, align: "center" });
  // exits
  T(s, "Exits", { x: 0.6, y: 4.3, w: 3, h: 0.35, fontSize: 18, bold: true, fontFace: THEME.headFontFace });
  const ex = [
    ["COMPLETED", "output schema ok and the registered verification gate accepts", C.accent1],
    ["BLOCKED", "a guard, a denied or pending approval, or the model said blocked", C.accent2],
    ["FAILED", "deadline, invalid turn, undeclared tool, gate rejection; typed code with numbers", C.accent4],
    ["CANCELLED", "caller cancelled; the in-flight model or tool call is interrupted", C.accent3],
  ];
  ex.forEach((e, i) => {
    const x = 0.6 + i * 3.05;
    s.addShape(SH.roundRect, { x, y: 4.85, w: 2.85, h: 1.65, rectRadius: 0.06, fill: { color: C.background2 }, line: { color: C.background2, width: 0 } });
    s.addShape(SH.ellipse, { x: x + 0.2, y: 5.05, w: 0.25, h: 0.25, fill: { color: e[2] }, line: { color: e[2], width: 0 } });
    T(s, e[0], { x: x + 0.55, y: 5.02, w: 2.2, h: 0.3, fontSize: 14, bold: true });
    T(s, e[1], { x: x + 0.2, y: 5.45, w: 2.5, h: 1.0, fontSize: 12 });
  });
}

// 5. Memory part 1: SOTA --------------------------------------------------------
{
  const s = slide("Memory 1/3: from a growing transcript to episodes, and what is still missing",
    "Explain the progression in plain terms. (1) Transcript in the window: simple, but it ends in truncation or a model-written summary, which is lossy and cannot be checked. (2) Retrieval memory: store chunks, fetch by similarity, but chunks have no relationship to each other and no notion of what was done versus what was seen. (3) Episodic memory: the unit is an experience with a time, a context and an outcome. Examples to cite: Generative Agents (Park et al. 2023) memory stream with recency, importance, relevance retrieval; MemGPT (Packer et al. 2023) tiered memory managed by the model; Reflexion (Shinn et al. 2023) verbal memory of past attempts. Verify these citations and add the exact venues before presenting. The gap sentence at the bottom is the thesis hook.");
  const cols = [
    ["Textual context", "Everything in the prompt; when full, truncate or let a model rewrite the older part as a paragraph.", "Lossy and unverifiable. The original is gone, so a wrong summary and a right one look the same.", C.accent3],
    ["Retrieval memory", "Chunks in a vector store, fetched by similarity to the current query.", "Chunks carry no structure: what was observed, what was done because of it, and what depended on what is lost.", C.accent5],
    ["Episodic memory", "The unit is an experience: one step with its context and outcome (Generative Agents, MemGPT, Reflexion).", "Right unit, but selection and summarisation are still heuristic or model-decided, and not checkable.", C.accent1],
  ];
  cols.forEach((c, i) => {
    const x = 0.6 + i * 4.15;
    s.addShape(SH.roundRect, { x, y: 1.6, w: 3.9, h: 3.85, rectRadius: 0.08, fill: { color: C.background2 }, line: { color: C.background2, width: 0 } });
    s.addShape(SH.ellipse, { x: x + 0.25, y: 1.8, w: 0.5, h: 0.5, fill: { color: c[3] }, line: { color: c[3], width: 0 } });
    T(s, String(i + 1), { x: x + 0.25, y: 1.8, w: 0.5, h: 0.5, align: "center", valign: "middle", bold: true, color: C.background1, fontSize: 16 });
    T(s, c[0], { x: x + 0.9, y: 1.8, w: 2.9, h: 0.5, fontSize: 20, bold: true, valign: "middle", fontFace: THEME.headFontFace });
    T(s, "What it does", { x: x + 0.25, y: 2.55, w: 3.4, h: 0.25, fontSize: 11, bold: true, color: C.accent3 });
    T(s, c[1], { x: x + 0.25, y: 2.85, w: 3.4, h: 1.0, fontSize: 13 });
    T(s, "Where it breaks", { x: x + 0.25, y: 3.95, w: 3.4, h: 0.25, fontSize: 11, bold: true, color: C.accent4 });
    T(s, c[2], { x: x + 0.25, y: 4.25, w: 3.4, h: 1.1, fontSize: 13 });
    if (i < 2) arrow(s, x + 3.9, 3.5, x + 4.15, 3.5);
  });
  s.addShape(SH.roundRect, { x: 0.6, y: 5.75, w: 12.1, h: 0.95, rectRadius: 0.06, fill: { color: C.text1 }, line: { color: C.text1, width: 0 } });
  T(s, "The gap: even with the right unit, nothing makes the choice of what to keep, or the summary that replaces the rest, checkable after the fact.", { x: 0.9, y: 5.8, w: 11.5, h: 0.85, fontSize: 16, color: C.background1, valign: "middle" });
}

// 6. Memory part 2: our architecture -----------------------------------------------
{
  const s = slide("Memory 2/3: our representation separates what is kept from what the model sees",
    "This is the core figure. Left: three durable stores that the harness owns. The model never holds the only copy of anything. Centre: a per-turn projection that decides what is visible. Right: the bounded context the model actually receives, with the budgets from ContextProjectionPolicy (12,000 estimated tokens overall, 2,000 for project state, 1,500 for stubs, 1,200 for semantic summaries). Trace one arrow: a tool call becomes an episode; its full result goes to the journal; PCKP decides whether the episode keeps its content; if not, the semantic path summarises it after archiving; the stub keeps a handle so the model can ask for the original. ProjectState is a different thing from episodes: it is the structured record of the task and changes only by evidence-linked transitions.");
  // left stores
  T(s, "Durable, harness-owned", { x: 0.6, y: 1.5, w: 3.2, h: 0.3, fontSize: 13, bold: true, color: C.accent3 });
  const st = [
    ["ProjectState", "decisions, artifacts, work items,\nquestions; evidence-linked", 1.85],
    ["Episode graph", "one per tool call; exploratory or\naction; OPEN, CLOSED, COMPACTED", 3.0],
    ["Result journal", "full output behind a\ncontent-hash handle", 4.15],
    ["Episode archive", "content-addressed snapshots,\nread back before release", 5.3],
  ];
  st.forEach((r) => {
    box(s, "", 0.6, r[2], 3.3, 1.0, { fill: { color: C.background2 }, line: { color: C.accent1, width: 1.5 } });
    T(s, r[0], { x: 0.75, y: r[2] + 0.08, w: 3.0, h: 0.3, fontSize: 14, bold: true, color: C.accent1 });
    T(s, r[1], { x: 0.75, y: r[2] + 0.4, w: 3.0, h: 0.55, fontSize: 11 });
  });
  // middle projection
  T(s, "Per-turn projection", { x: 4.9, y: 1.5, w: 3.3, h: 0.3, fontSize: 13, bold: true, color: C.accent3 });
  box(s, "PCKP compaction\nexact, dependency-closed", 4.9, 2.95, 3.3, 0.95, { fill: { color: C.accent2 }, line: { color: C.accent2, width: 0 }, color: C.background1, bold: true });
  box(s, "Verified semantic summaries\nonly for episodes not kept", 4.9, 4.1, 3.3, 0.95, { fill: { color: C.accent2 }, line: { color: C.accent2, width: 0 }, color: C.background1, bold: true });
  box(s, "Stubs: one line + handle\nfor dropped content", 4.9, 5.25, 3.3, 0.95, { fill: { color: C.accent2 }, line: { color: C.accent2, width: 0 }, color: C.background1, bold: true });
  arrow(s, 3.9, 3.5, 4.9, 3.4);
  arrow(s, 3.9, 5.7, 4.9, 4.6);
  arrow(s, 6.55, 3.9, 6.55, 4.1);
  arrow(s, 6.55, 5.05, 6.55, 5.25);
  // right context
  T(s, "ModelContext  (12,000 est. tokens)", { x: 9.2, y: 1.5, w: 3.6, h: 0.3, fontSize: 13, bold: true, color: C.accent3 });
  const ctx = [
    ["Project state view", "<= 2,000"],
    ["Stable prefix: prompt, schema", "cache-friendly, byte-identical"],
    ["Recent observations", "newest that fit"],
    ["Semantic summaries + sources", "<= 1,200"],
    ["Stubs", "<= 1,500"],
  ];
  ctx.forEach((c, i) => {
    const y = 1.85 + i * 0.85;
    box(s, "", 9.2, y, 3.5, 0.7, { fill: { color: C.background1 }, line: { color: C.accent4, width: 1.25 } });
    T(s, c[0], { x: 9.3, y: y + 0.07, w: 3.3, h: 0.3, fontSize: 12, bold: true });
    T(s, c[1], { x: 9.3, y: y + 0.38, w: 3.3, h: 0.25, fontSize: 11, color: C.accent3 });
  });
  arrow(s, 3.9, 2.2, 9.2, 2.2, { color: C.accent5, dash: "dash" });
  arrow(s, 8.2, 3.4, 9.2, 3.9);
  arrow(s, 8.2, 4.6, 9.2, 4.75);
  arrow(s, 8.2, 5.7, 9.2, 5.6);
  T(s, "journal handle: the model can ask for the original (get_tool_result)", { x: 4.9, y: 6.3, w: 7.8, h: 0.35, fontSize: 12, color: C.accent3, italic: true });
  T(s, "Summaries are derived text. They name their source episodes and are never evidence in themselves.", { x: 4.9, y: 6.65, w: 7.8, h: 0.4, fontSize: 12, bold: true });
}

// 7. Memory part 3: why ----------------------------------------------------------
{
  const s = slide("Memory 3/3: why we chose it: a wrong answer can be traced to a layer",
    "Make the argument as a debugging story, not as a feature list. Example: at iteration 37 the agent reports a wrong checksum. With a rolling text summary there is one paragraph and no way to tell whether the tool returned the wrong value, the summariser dropped it, or the model misread it. With this design each of those is a separate record you can open. The cost is real: more machinery and roughly 28% more visible tokens in our synthetic benchmark. State that cost out loud.");
  T(s, "Same failure, two memories", { x: 0.6, y: 1.5, w: 6, h: 0.35, fontSize: 18, bold: true, fontFace: THEME.headFontFace });
  T(s, "Iteration 37: the agent reports a wrong file checksum.", { x: 0.6, y: 1.9, w: 7, h: 0.35, fontSize: 14, italic: true });
  // left column: text
  s.addShape(SH.roundRect, { x: 0.6, y: 2.5, w: 3.6, h: 4.2, rectRadius: 0.08, fill: { color: C.background2 }, line: { color: C.background2, width: 0 } });
  T(s, "Rolling text summary", { x: 0.8, y: 2.62, w: 3.2, h: 0.35, fontSize: 15, bold: true, color: C.accent4 });
  T(s, bullets(["Older turns are one model-written paragraph", "The original text is gone", "Tool error, summariser loss and model misread look identical", "Fix is a guess"], { gap: 8 }), { x: 0.8, y: 3.1, w: 3.2, h: 3.4, fontSize: 13 });
  // middle: ours
  s.addShape(SH.roundRect, { x: 4.4, y: 2.5, w: 4.1, h: 4.2, rectRadius: 0.08, fill: { color: C.background2 }, line: { color: C.accent1, width: 2 } });
  T(s, "This architecture", { x: 4.6, y: 2.62, w: 3.7, h: 0.35, fontSize: 15, bold: true, color: C.accent1 });
  const steps = ["Open the context-projected event for turn 37: what the model saw", "Read the summary's source episode ids and handles", "Read its verification record: which check passed", "Rehydrate the source from the archive and compare", "Name the layer: tool, selection, summary or model"];
  steps.forEach((t, i) => {
    const y = 3.1 + i * 0.7;
    s.addShape(SH.ellipse, { x: 4.6, y: y + 0.02, w: 0.36, h: 0.36, fill: { color: C.accent1 }, line: { color: C.accent1, width: 0 } });
    T(s, String(i + 1), { x: 4.6, y: y + 0.02, w: 0.36, h: 0.36, align: "center", valign: "middle", color: C.background1, bold: true, fontSize: 12 });
    T(s, t, { x: 5.1, y, w: 3.25, h: 0.65, fontSize: 12 });
  });
  // right: mapping
  T(s, "What each choice buys", { x: 8.9, y: 2.5, w: 3.9, h: 0.35, fontSize: 15, bold: true });
  const map = [
    ["Typed episodes", "dependencies can be enforced and followed", "R3"],
    ["Journal + archive", "nothing dropped is unrecoverable", "R2"],
    ["Exact selection", "the keep/drop decision is reproducible", "R1, R3"],
    ["Verified summaries", "a summary either quotes its sources or is rejected", "R2, R3"],
  ];
  map.forEach((m, i) => {
    const y = 3.0 + i * 0.95;
    T(s, m[0], { x: 8.9, y, w: 3.0, h: 0.3, fontSize: 13, bold: true, color: C.accent1 });
    T(s, m[1], { x: 8.9, y: y + 0.3, w: 3.1, h: 0.55, fontSize: 12 });
    T(s, m[2], { x: 12.05, y, w: 0.7, h: 0.3, fontSize: 12, bold: true, color: C.accent2, align: "right" });
  });
}

// 8. PCKP -------------------------------------------------------------------------------
{
  const s = slide("Compaction is a constrained optimisation, not a truncation rule",
    "Explain the constraint with the picture: an action episode is only meaningful together with the observation it consumed. Dropping the observation and keeping the action leaves a conclusion without its premise. So the problem is a knapsack where choosing an item forces its prerequisites: a precedence-constrained knapsack. The SDK solves rooted forests exactly with a dynamic program and falls back to branch and bound with a node cap, in which case the certificate says best-effort. Be honest about the limit: the exactness is of the optimisation, the utility weights (relevance 0.40, centrality 0.20, provenance 0.20, recency 0.10, frequency 0.05) are hand-set.");
  // mini DAG
  T(s, "Illustrative: budget 3 units; cost / u = utility. Keep what maximises utility, prerequisites included.", { x: 0.6, y: 1.5, w: 7, h: 0.35, fontSize: 14, bold: true });
  const nodes = [
    { id: "e1", t: "read config\ncost 1  u 2", x: 0.8, y: 2.3, c: C.accent1 },
    { id: "e2", t: "list dir\ncost 1  u 1", x: 3.2, y: 2.3, c: C.accent3 },
    { id: "e3", t: "read log\ncost 2  u 3", x: 5.6, y: 2.3, c: C.accent3 },
    { id: "a1", t: "edit file\ncost 1  u 4", x: 0.8, y: 4.3, c: C.accent2 },
    { id: "a2", t: "run tests\ncost 1  u 2", x: 4.4, y: 4.3, c: C.accent3 },
  ];
  nodes.forEach((n) => box(s, n.t, n.x, n.y, 1.9, 1.05, { fill: { color: n.c }, line: { color: n.c, width: 0 }, color: C.background1, fontSize: 12, bold: true }));
  arrow(s, 1.75, 4.3, 1.75, 3.35);
  arrow(s, 5.35, 4.3, 6.2, 3.35);
  arrow(s, 5.35, 4.3, 4.15, 3.35);
  T(s, "action  ->  the observation it consumed", { x: 0.6, y: 5.6, w: 6, h: 0.3, fontSize: 12, color: C.accent3 });
  const lg = [[C.accent2, "mandatory: latest batch, open, prerequisites"], [C.accent1, "kept by the solver"], [C.accent3, "compacted to a stub"]];
  lg.forEach((l, i) => {
    s.addShape(SH.rect, { x: 0.8, y: 6.0 + i * 0.32, w: 0.22, h: 0.22, fill: { color: l[0] }, line: { color: l[0], width: 0 } });
    T(s, l[1], { x: 1.15, y: 5.97 + i * 0.32, w: 5, h: 0.28, fontSize: 12 });
  });
  // right text
  T(s, "What the solver returns", { x: 8.3, y: 1.5, w: 4.5, h: 0.35, fontSize: 16, bold: true, color: C.accent1 });
  T(s, bullets(["A selection closed under prerequisites", "Status: optimal, best-effort or infeasible-mandatory", "Upper bound, optimality gap, branch nodes, problem hash"], { gap: 6 }), { x: 8.3, y: 1.95, w: 4.5, h: 1.9, fontSize: 13 });
  T(s, "Why exact, and where it stops", { x: 8.3, y: 4.0, w: 4.5, h: 0.35, fontSize: 16, bold: true, color: C.accent2 });
  T(s, bullets(["A greedy rule can keep an action and drop its premise; the closure rules that out", "The certificate makes the choice reproducible and auditable", "Limit: utility weights are hand-set, so optimal for a stated utility, not for task success"], { gap: 6 }), { x: 8.3, y: 4.45, w: 4.5, h: 2.3, fontSize: 13 });
}

// 9. Semantic summaries ------------------------------------------------------------------
{
  const s = slide("A summary is a checked claim about episodes, never a replacement for them",
    "Pipeline, left to right. Archive first, and read it back. Then a summariser proposes a digest; the default one is extractive and needs no model. Then deterministic checks decide: sources intact, dependencies resolve, citations stay inside the group, each claim quotes its cited episodes verbatim, every failed or blocked episode is mentioned, no number or identifier appears that the sources do not contain, compression within ceiling. Only if all pass can the optional judges run, and they can only veto, which is visible in _semantic_stages because judges run only when the deterministic report is clean and only add checks. A rejected group keeps its payloads and retries up to a limit. The release of the payload is the last step.");
  const y = 1.9, h = 1.25, w = 2.15;
  const stages = [
    ["1  Archive", "content-addressed,\nread back and\ncompared", C.accent5],
    ["2  Summarise", "extractive by default;\nhost model optional", C.accent3],
    ["3  Verify", "deterministic checks\ndecide", C.accent1],
    ["4  Judges", "may only veto", C.accent3],
    ["5  Commit", "release payload,\nlink summary + archive", C.accent2],
  ];
  stages.forEach((st, i) => {
    const x = 0.6 + i * 2.5;
    box(s, "", x, y, w, h, { fill: { color: st[2] }, line: { color: st[2], width: 0 } });
    T(s, st[0], { x: x + 0.12, y: y + 0.08, w: w - 0.24, h: 0.32, fontSize: 14, bold: true, color: C.background1 });
    T(s, st[1], { x: x + 0.12, y: y + 0.45, w: w - 0.24, h: 0.8, fontSize: 12, color: C.background1 });
    if (i < 4) arrow(s, x + w, y + h / 2, x + 2.5, y + h / 2);
  });
  arrow(s, 6.1, y + h, 6.1, 3.75, { color: C.accent4, dash: "dash" });
  T(s, "any failure: keep originals, name the failing check, retry up to a limit", { x: 6.3, y: 3.5, w: 6.4, h: 0.3, fontSize: 12, color: C.accent4 });
  T(s, "What the deterministic checks look at", { x: 0.6, y: 4.0, w: 6, h: 0.35, fontSize: 16, bold: true, color: C.accent1 });
  const checks = ["Archive entries present and unchanged", "Dependencies resolve in the store", "Citations stay inside the group", "Claims and decisions quote cited text", "Every failed or blocked episode appears", "No number or identifier absent from sources", "Compression within token ceiling", "Hierarchy level bounded (consolidation)"];
  checks.forEach((c, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.6 + col * 4.1, yy = 4.45 + row * 0.6;
    s.addShape(SH.ellipse, { x, y: yy + 0.06, w: 0.2, h: 0.2, fill: { color: C.accent1 }, line: { color: C.accent1, width: 0 } });
    T(s, c, { x: x + 0.35, y: yy, w: 3.6, h: 0.5, fontSize: 13 });
  });
  T(s, "Reversible by design", { x: 8.9, y: 4.0, w: 3.9, h: 0.35, fontSize: 16, bold: true, color: C.accent2 });
  T(s, "Consolidation keeps children for provenance. Eviction only touches archived, summarised records. Rehydration restores originals within a token budget.", { x: 8.9, y: 4.45, w: 3.8, h: 2.2, fontSize: 13 });
}

// 10. Governance + state -----------------------------------------------------------------
{
  const s = slide("The model may name a tool or an outcome; only the harness can make either happen",
    "Two mechanisms with the same principle. Left: a tool call passes through ordered deterministic checks, and each refusal has a typed code so the failure explains itself. Right: working memory (ProjectState) changes only through typed transitions that must carry evidence; there are nine kinds. The model proposes, the reducer applies. This is the SDK scope decision: no domain roles are built in. Tools, skills and permissions arrive from the caller.");
  T(s, "Governed tool call", { x: 0.6, y: 1.5, w: 5, h: 0.35, fontSize: 18, bold: true, fontFace: THEME.headFontFace });
  const g = ["Declared in the definition?", "Arguments match the JSON schema?", "Pre-tool hooks", "Sensitive paths, capability grant, containment", "Typed approval, with DecisionSource", "Handler under a watchdog"];
  g.forEach((t, i) => {
    const y = 2.0 + i * 0.78;
    box(s, t, 0.6, y, 4.7, 0.58, { fill: { color: C.background2 }, align: "left", fontSize: 13, margin: 0.15 });
    if (i < g.length - 1) arrow(s, 2.95, y + 0.58, 2.95, y + 0.78);
  });
  T(s, "Each refusal is typed: an undeclared tool ends FAILED; bad arguments return to the model as a correctable observation; a denial or pending approval ends BLOCKED.", { x: 5.55, y: 2.1, w: 2.2, h: 3.0, fontSize: 12, color: C.accent4, italic: true });
  // right
  T(s, "Working memory by transition", { x: 8.0, y: 1.5, w: 5, h: 0.35, fontSize: 18, bold: true, fontFace: THEME.headFontFace });
  box(s, "Model / tool outcome\n(a proposal)", 8.0, 2.1, 4.7, 0.9, { fill: { color: C.accent4 }, line: { color: C.accent4, width: 0 }, color: C.background1, bold: true });
  arrow(s, 10.35, 3.0, 10.35, 3.4);
  box(s, "StateTransition: kind + actor + evidence (at least one)", 8.0, 3.4, 4.7, 0.8, { fill: { color: C.background2 }, line: { color: C.accent1, width: 1.5 } });
  arrow(s, 10.35, 4.2, 10.35, 4.6);
  box(s, "Reducer applies it; new revision, hash-chained event", 8.0, 4.6, 4.7, 0.8, { fill: { color: C.accent1 }, line: { color: C.accent1, width: 0 }, color: C.background1, bold: true });
  T(s, "Nine transition kinds; authority is harness, controller or human. A decision is OPEN, LOCKED or SUPERSEDED.", { x: 8.0, y: 5.65, w: 4.7, h: 1.0, fontSize: 13 });
}

// 11. Evidence -------------------------------------------------------------------------------
{
  const s = slide("Evidence is kept as independent records that vouch for each other",
    "Do not claim more than the code does. Each chain alone detects an edit in the middle. Removal of the newest entries, or a resealed chain, is detected only against a record kept outside the run root: the signed run-head checkpoint. The terminal-link ties telemetry and audit together at the end of the run. The verdicts are ok, truncated, diverged, advanced and pruned. Checkpointing is opt-in. The developer tool diagnose-run reads these ledgers and reports the terminal finding, the first problem, the first error and, for a graph run, the failing nodes with what each held up; it writes nothing.");
  s.addShape(SH.roundRect, { x: 0.6, y: 1.6, w: 6.6, h: 4.6, rectRadius: 0.08, fill: { color: C.background2 }, line: { color: C.accent3, width: 1, dashType: "dash" } });
  T(s, "Inside the run root", { x: 0.8, y: 1.7, w: 4, h: 0.3, fontSize: 13, bold: true, color: C.accent3 });
  box(s, "Telemetry ledger\nSQLite, per-run hash chain", 0.85, 2.2, 2.85, 1.0, { fill: { color: C.background1 } });
  box(s, "Audit transcript\nJSONL, hash chain, fsync", 4.15, 2.2, 2.8, 1.0, { fill: { color: C.background1 } });
  arrow(s, 3.7, 2.5, 4.15, 2.5);
  arrow(s, 4.15, 2.9, 3.7, 2.9);
  T(s, "terminal-link: each holds the other\u2019s head hash", { x: 0.85, y: 3.3, w: 6.1, h: 0.3, fontSize: 12, color: C.accent2, align: "center", bold: true });
  box(s, "Tool results\ncontent-hash handle", 0.85, 3.9, 3.0, 0.9, { fill: { color: C.background1 } });
  box(s, "Project state\nstate hash + event chain", 3.95, 3.9, 3.0, 0.9, { fill: { color: C.background1 } });
  box(s, "Graph run record\nrun hash + history-prefix hash", 0.85, 5.0, 6.1, 0.9, { fill: { color: C.background1 } });
  T(s, "Outside it", { x: 7.7, y: 1.7, w: 3, h: 0.3, fontSize: 13, bold: true, color: C.accent3 });
  box(s, "Signed run-head checkpoint\nHMAC over sequence + head hash\nof both chains", 7.7, 2.2, 5.0, 1.2, { fill: { color: C.accent2 }, line: { color: C.accent2, width: 0 }, color: C.background1, bold: true });
  arrow(s, 6.95, 2.7, 7.7, 2.7);
  box(s, "Retention tombstone anchor", 7.7, 3.7, 5.0, 0.7, { fill: { color: C.background2 } });
  T(s, "Verdicts", { x: 7.7, y: 4.65, w: 5, h: 0.3, fontSize: 14, bold: true });
  T(s, "ok  |  truncated  |  diverged  |  advanced  |  pruned", { x: 7.7, y: 5.0, w: 5, h: 0.4, fontSize: 14, color: C.accent1, bold: true });
  T(s, "Opt-in: nothing is checkpointed unless a checkpointer is configured.", { x: 7.7, y: 5.5, w: 5, h: 0.6, fontSize: 12, italic: true });
  T(s, "diagnose-run turns the ledgers into an answer: terminal finding, first problem, first error, failing nodes and what each held up.", { x: 0.6, y: 6.35, w: 12.1, h: 0.55, fontSize: 14 });
}

// 12. Multi-agent ------------------------------------------------------------------------------
{
  const s = slide("Many agents: typed exchange, and growth the plan can bound",
    "Agents never converse. Lateral data moves only as typed discoveries and immutable values in graph state, routed by exact reference. An agent whose profile grants graph.elastic.request can queue requests to explore beyond the plan. They are judged at commit, atomically with the node result: unique ids, visible dependencies, narrowing references, node and depth caps where a batch of N costs N+1 because of the join. A batch that does not fit is deferred whole until a human grants capacity or declines. Ceilings from policy refuse outright. Per-wave reservations stop siblings from racing. After a crash only idempotent nodes are replayed.");
  box(s, "Plan  (validated, approved)", 0.6, 1.7, 3.3, 0.7, { fill: { color: C.background2 } });
  arrow(s, 2.25, 2.4, 2.25, 2.85);
  box(s, "Wave: independent nodes in parallel\neach a fresh BaseAgent", 0.6, 2.85, 3.3, 1.1, { fill: { color: C.accent1 }, line: { color: C.accent1, width: 0 }, color: C.background1, bold: true });
  arrow(s, 2.25, 3.95, 2.25, 4.4);
  box(s, "Atomic commit in node-id order\nresult + discoveries + requests", 0.6, 4.4, 3.3, 1.1, { fill: { color: C.text2 }, line: { color: C.text2, width: 0 }, color: C.background1, bold: true });
  arrow(s, 3.9, 4.7, 5.0, 2.72);
  arrow(s, 3.9, 4.95, 5.0, 4.07);
  arrow(s, 3.9, 5.2, 5.0, 5.42);
  T(s, "Admission at commit", { x: 5.0, y: 1.7, w: 4, h: 0.35, fontSize: 16, bold: true, color: C.accent2 });
  const adm = [["fits caps", "one exploration node per request + one join; dependents wait behind the join", C.accent1], ["exceeds plan caps", "whole batch deferred; join blocked until grant or decline", C.accent2], ["passes a ceiling", "refused with a typed code; agent told at once", C.accent4]];
  adm.forEach((a, i) => {
    const y = 2.15 + i * 1.35;
    s.addShape(SH.roundRect, { x: 5.0, y, w: 7.7, h: 1.15, rectRadius: 0.06, fill: { color: C.background2 }, line: { color: C.background2, width: 0 } });
    s.addShape(SH.ellipse, { x: 5.2, y: y + 0.4, w: 0.35, h: 0.35, fill: { color: a[2] }, line: { color: a[2], width: 0 } });
    T(s, a[0], { x: 5.75, y: y + 0.12, w: 6.7, h: 0.35, fontSize: 15, bold: true });
    T(s, a[1], { x: 5.75, y: y + 0.5, w: 6.7, h: 0.6, fontSize: 13 });
  });
  T(s, "Crash safety: a node left RUNNING is replayed only if its binding is declared idempotent; otherwise it fails as interrupted-non-idempotent.", { x: 5.0, y: 6.25, w: 7.7, h: 0.6, fontSize: 13, italic: true });
}

// 13. Contributions ----------------------------------------------------------------------------
{
  const s = slide("What we claim, against what is already established",
    "This is the slide the supervisor will push on. Left column is what we adopt without claiming. Middle is our claimed delta, stated as a property, not as a score. Right is the honest status of the evidence for each: implemented and covered by tests, measured on a synthetic benchmark, or not yet evaluated. Do not let the status column be skipped. The literature positioning (MemGPT, Generative Agents, Reflexion, LangGraph checkpointing, OpenTelemetry GenAI conventions) still needs a proper related-work pass.");
  const rows = [
    ["Established", "Our delta", "Evidence status"],
    ["Episodic memory notion", "Typed episode DAG; exploratory/action dependency rule enforced in code", "Implemented, unit-tested"],
    ["Budgeted context selection", "Exact precedence-constrained knapsack with a solver certificate", "Implemented; exact vs greedy on small cases"],
    ["LLM summarisation of old context", "Archive-before-release, deterministic grounding checks, judges veto only", "Implemented; synthetic benchmark only"],
    ["Tracing (OpenTelemetry-style)", "Cross-linked hash chains, signed run-head checkpoints, diagnose-run", "Implemented, unit-tested; no user study"],
    ["Graph orchestration", "Agent-initiated growth with commit-time admission and ceilings", "Implemented; not benchmarked"],
  ];
  const tbl = rows.map((r, ri) => r.map((c, ci) => ({
    text: c,
    options: {
      bold: ri === 0 || ci === 0,
      fontSize: ri === 0 ? 13 : 12,
      color: ri === 0 ? C.background1 : (ci === 2 ? C.accent2 : C.text1),
      fill: { color: ri === 0 ? C.text2 : (ri % 2 ? C.background1 : C.background2) },
      valign: "middle",
    },
  })));
  s.addTable(tbl, { x: 0.6, y: 1.6, w: 12.1, colW: [3.1, 5.6, 3.4], rowH: [0.45, 0.85, 0.85, 0.85, 0.85, 0.85], border: { type: "solid", color: C.accent3, pt: 0.5 }, fontFace: THEME.bodyFontFace, margin: [0.05, 0.1, 0.05, 0.1] });
}

// 14. Evidence chart ----------------------------------------------------------------------------
{
  const s = slide("Measured so far: semantic compaction roughly doubles what survives compaction",
    "Numbers are means over seeds 7, 8, 9 from benchmarks/results/memory-benchmark-report.json, 240 synthetic episodes, episode budget 3,000 and summary budget 1,200 estimated tokens. PCKP-only: failure F1 0.434, task reconstruction 0.193, visible context 4,135 tokens. With extractive semantic summaries: 0.893, 0.428, 5,290 tokens. Archive retrieval 0.695, and 0.834 when the active graph is bounded to 60 records. Say clearly: the reader is deterministic text matching, not a model, so this bounds recoverability and says nothing about reasoning. Even after semantic compaction more than half the needed facts are not directly visible.");
  s.addChart(pres.charts.BAR, [
    { name: "PCKP only", labels: ["Failure recall (F1)", "Task reconstruction", "Direct checksum recall"], values: [0.434, 0.193, 0.146] },
    { name: "PCKP + semantic", labels: ["Failure recall (F1)", "Task reconstruction", "Direct checksum recall"], values: [0.893, 0.428, 0.224] },
  ], {
    x: 0.6, y: 1.55, w: 7.4, h: 5.0, barDir: "col", barGrouping: "clustered",
    chartColors: ["6B7F86", "1F7A72"],
    showLegend: true, legendPos: "b", legendFontSize: 12, legendColor: "12262B", legendFontFace: "+mn-lt",
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 12, dataLabelColor: "12262B", dataLabelFontFace: "+mn-lt", dataLabelFormatCode: "0.00",
    catAxisLabelColor: "12262B", catAxisLabelFontSize: 12, catAxisLabelFontFace: "+mn-lt",
    valAxisLabelColor: "6B7F86", valAxisLabelFontSize: 11, valAxisLabelFontFace: "+mn-lt", valAxisMaxVal: 1, valAxisMinVal: 0,
    valGridLine: { color: "D5DEDC", size: 0.5 }, catGridLine: { style: "none" },
    showTitle: true, title: "Recoverable from the visible context, 240 episodes, 3 seeds", titleFontSize: 13, titleColor: "12262B", titleFontFace: "+mn-lt",
  });
  T(s, "Cost", { x: 8.5, y: 1.6, w: 4.2, h: 0.3, fontSize: 14, bold: true, color: C.accent4 });
  T(s, "+28%", { x: 8.5, y: 1.95, w: 2.0, h: 0.8, fontSize: 40, bold: true, color: C.accent4, fontFace: THEME.headFontFace });
  T(s, "visible tokens (4,135 to 5,290)", { x: 10.4, y: 2.15, w: 2.4, h: 0.6, fontSize: 12 });
  T(s, "Still not recoverable", { x: 8.5, y: 3.05, w: 4.2, h: 0.3, fontSize: 14, bold: true, color: C.accent2 });
  T(s, "0.57", { x: 8.5, y: 3.4, w: 2.0, h: 0.8, fontSize: 40, bold: true, color: C.accent2, fontFace: THEME.headFontFace });
  T(s, "of needed facts are not in the visible context (1 minus 0.43); the archive returns 0.70 of them", { x: 10.4, y: 3.55, w: 2.4, h: 1.0, fontSize: 12 });
  T(s, "Read with care", { x: 8.5, y: 4.7, w: 4.2, h: 0.3, fontSize: 14, bold: true });
  T(s, bullets(["Reader is text matching, not a model", "One synthetic generator, three seeds", "No external baseline yet"], { gap: 4 }), { x: 8.5, y: 5.05, w: 4.2, h: 1.5, fontSize: 13 });
}

// 15. Threats and next steps ---------------------------------------------------------------------
{
  const s = slide("What would make this a thesis: four studies the current evidence does not cover",
    "Close by owning the weaknesses, then ask for guidance. Each study maps to a requirement. Study A addresses the biggest gap: no end-to-end model in the benchmark. Study B is the baseline comparison needed for any state-of-the-art claim. Study C tests the hand-set utility weights. Study D tests R3 directly with people, because diagnosability is the claim that is hardest to show with synthetic numbers. Questions for the supervisor are at the bottom.");
  const st = [
    ["A", "End-to-end with real models", "Same tasks, model in the loop, task success and cost; ablate each layer.", "R1, R2"],
    ["B", "Baselines on identical episodes", "Rolling LLM summary, MemGPT-style paging, retrieval-only memory.", "R1, R2"],
    ["C", "Is the utility model right?", "Ablate and learn the weights; compare with exact optimum under each.", "R1"],
    ["D", "Does it shorten diagnosis?", "Seeded faults in tool, selection, summary and model; time-to-locate with and without the ledgers.", "R3"],
  ];
  st.forEach((r, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.6 + col * 6.15, y = 1.6 + row * 2.3;
    s.addShape(SH.roundRect, { x, y, w: 5.95, h: 2.1, rectRadius: 0.08, fill: { color: C.background2 }, line: { color: C.background2, width: 0 } });
    s.addShape(SH.ellipse, { x: x + 0.25, y: y + 0.25, w: 0.6, h: 0.6, fill: { color: C.accent1 }, line: { color: C.accent1, width: 0 } });
    T(s, r[0], { x: x + 0.25, y: y + 0.25, w: 0.6, h: 0.6, align: "center", valign: "middle", bold: true, color: C.background1, fontSize: 18 });
    T(s, r[1], { x: x + 1.05, y: y + 0.25, w: 4.0, h: 0.6, fontSize: 17, bold: true, valign: "middle", fontFace: THEME.headFontFace });
    T(s, r[3], { x: x + 4.9, y: y + 0.25, w: 0.85, h: 0.6, fontSize: 13, bold: true, color: C.accent2, align: "right", valign: "middle" });
    T(s, r[2], { x: x + 0.25, y: y + 1.05, w: 5.45, h: 0.95, fontSize: 14 });
  });
  s.addShape(SH.roundRect, { x: 0.6, y: 6.25, w: 12.1, h: 0.75, rectRadius: 0.06, fill: { color: C.text1 }, line: { color: C.text1, width: 0 } });
  T(s, "For discussion: which of A to D is the strongest first paper, and is diagnosability a defensible primary claim?", { x: 0.85, y: 6.28, w: 11.6, h: 0.7, fontSize: 15, color: C.background1, valign: "middle" });
}

(async () => {
  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT);
})();
