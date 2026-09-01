/* ==================================================================
   HiKO 侧边导航 · 弹窗面板内容
   ------------------------------------------------------------------
   注意：本文件中的数值目前均为「示例数据」，用于验证视觉与交互。
   后续接入真实数据时，替换各 render() 中的硬编码数字即可，
   可对接首页已有接口：/get_hr /get_wave_data /get_openface
   ================================================================== */

/* ==================================================================
   DASS-21 抑郁-焦虑-压力量表
   ------------------------------------------------------------------
   来源：/home/lsz/HIKO/量表.txt（2026-08-12 用户提供的权威版本）
   题目与选项文字逐字照录，未做任何改写 —— 量表的信效度建立在
   原始措辞上，改词就等于换了一个未经验证的量表。

   为什么用 DASS-21 单一量表：
     它一次作答即可拆出 抑郁(D) / 焦虑(A) / 压力(S) 三个分数，
     正好一对一填充「心理量表估算」模块的三项，无需多份问卷。
     （旧版界面的 PSS-10 / GAD-7 / PHQ-9 / PSQI 均不在权威文档中，
       且 GAD-7 与 DASS-A 测同一构念，同时存在会给出两个不一致的
       焦虑分数，用户无法判断该信哪个。）
   ================================================================== */
const DASS21 = {
  code: 'DASS21',
  name: '抑郁-焦虑-压力量表',
  /* 4 级选项，0~3 分。文字照录文档。 */
  options: [
    { v:0, label:'不符合',   short:'不符合' },
    { v:1, label:'有时符合', short:'有时' },
    { v:2, label:'经常符合', short:'经常' },
    { v:3, label:'非常符合', short:'非常' }
  ],
  /* 21 题，逐字照录。dim 为该题所属分量表，用于计分与「本题计入」提示。 */
  items: [
    { n:1,  dim:'S', text:'我觉得很难让自己平静下来。' },
    { n:2,  dim:'A', text:'我感到口干。' },
    { n:3,  dim:'D', text:'我好像无法再有任何愉快、舒畅的感觉。' },
    { n:4,  dim:'A', text:'我感到呼吸困难（例如：呼吸急促、透不过气，并且不是因为体力消耗造成的）。' },
    { n:5,  dim:'D', text:'我感到很难主动去开始做事情。' },
    { n:6,  dim:'S', text:'我对事情往往反应过度。' },
    { n:7,  dim:'A', text:'我感到颤抖（例如：双手发抖）。' },
    { n:8,  dim:'S', text:'我觉得自己消耗了很多精力在紧张焦虑上。' },
    { n:9,  dim:'A', text:'我担心一些让自己惊慌或出丑的场合。' },
    { n:10, dim:'D', text:'我觉得自己对未来没有什么可期待的。' },
    { n:11, dim:'S', text:'我发现自己很容易心烦意乱。' },
    { n:12, dim:'S', text:'我感到很难放松下来。' },
    { n:13, dim:'D', text:'我感到忧郁、沮丧。' },
    { n:14, dim:'S', text:'对任何阻碍我继续完成手头工作的事情，我都无法容忍。' },
    { n:15, dim:'A', text:'我感到自己接近恐慌。' },
    { n:16, dim:'D', text:'我对任何事情都无法产生热情。' },
    { n:17, dim:'D', text:'我觉得自己作为一个人没什么价值。' },
    { n:18, dim:'S', text:'我感觉自己很容易被激怒。' },
    { n:19, dim:'A', text:'即使在没有体力消耗的情况下，我也能感觉到自己的心跳（例如：感到心率加快、心跳漏拍）。' },
    { n:20, dim:'A', text:'我无缘无故地感到害怕。' },
    { n:21, dim:'D', text:'我觉得生活毫无意义。' }
  ],
  /* 分量表题号。与 items 的 dim 互为冗余，构造时会交叉校验，
     防止今后改动其中一处而另一处忘改（这类不一致是静默算错分的根源）。 */
  groups: {
    D: { key:'D', label:'抑郁', full:'抑郁 Depression', items:[3,5,10,13,16,17,21] },
    A: { key:'A', label:'焦虑', full:'焦虑 Anxiety',    items:[2,4,7,9,15,19,20] },
    S: { key:'S', label:'压力', full:'压力 Stress',     items:[1,6,8,11,12,14,18] }
  },
  /* 严重程度切分点，针对 ×2 之后的分数（文档「三、严重程度分级」）。
     max 用 Infinity 表示「≥」。 */
  cutoffs: {
    D: [ {lvl:'正常',max:9},  {lvl:'轻度',max:13}, {lvl:'中度',max:20}, {lvl:'重度',max:27}, {lvl:'极重度',max:Infinity} ],
    A: [ {lvl:'正常',max:7},  {lvl:'轻度',max:9},  {lvl:'中度',max:14}, {lvl:'重度',max:19}, {lvl:'极重度',max:Infinity} ],
    S: [ {lvl:'正常',max:14}, {lvl:'轻度',max:18}, {lvl:'中度',max:25}, {lvl:'重度',max:33}, {lvl:'极重度',max:Infinity} ]
  },
  /* ×2 的由来：DASS-21 是 DASS-42 的半量表，原始分必须 ×2 才能与
     DASS-42 的标准切分点对齐。不乘 2 则单项满分仅 21 分，
     永远达不到「极重度 ≥28」—— 这个错误不会报错，只会一直算低。 */
  RAW_MULTIPLIER: 2,
  SCORE_MAX: 42            // 7题 × 3分 × 2
};

/* ---- 构造期自检：分组与题目必须一致，否则直接抛错而不是静默算错 ---- */
(function verifyDASS21(){
  const all = [].concat(DASS21.groups.D.items, DASS21.groups.A.items, DASS21.groups.S.items)
                .sort((a,b)=>a-b);
  const expect = Array.from({length:21},(_,i)=>i+1);
  if (all.length !== 21 || all.join(',') !== expect.join(','))
    throw new Error('DASS-21 分组异常：21 题未被恰好覆盖一次 -> ' + all.join(','));
  DASS21.items.forEach(it=>{
    if (!DASS21.groups[it.dim].items.includes(it.n))
      throw new Error(`DASS-21 第${it.n}题 dim=${it.dim} 与 groups 不一致`);
  });
  Object.values(DASS21.groups).forEach(g=>{
    if (g.items.length !== 7) throw new Error(`DASS-21 ${g.key} 分量表题数应为7，实为${g.items.length}`);
  });
})();

/* ------------------------------------------------------------------
   作答状态。放在模块级而非 render 内的原因：
   外壳的 buildPopup() 每次打开面板都会重建 DOM，
   状态若存在 DOM 或闭包里，关闭再打开就全丢了。
   ------------------------------------------------------------------ */
const DASS21_STATE = {
  answers: {},             // { 题号: 0..3 }
  get answeredCount(){ return Object.keys(this.answers).length; },
  get isComplete(){ return this.answeredCount === DASS21.items.length; },
  reset(){ this.answers = {}; }
};

/* 计分。仅当某分量表 7 题全部作答才输出分数，
   缺一题就返回 null —— 缺项求和会得到一个偏低但看起来正常的分数，
   这种「看似合理的错值」比缺失更危险。 */
function scoreDASS21(answers){
  const out = { complete:true, subscales:{} };
  Object.values(DASS21.groups).forEach(g=>{
    const vals = g.items.map(n=>answers[n]);
    const missing = g.items.filter(n=>answers[n] === undefined);
    if (missing.length){
      out.complete = false;
      out.subscales[g.key] = { key:g.key, label:g.label, full:g.full,
        raw:null, score:null, level:null, pct:null,
        answered:7-missing.length, missing };
      return;
    }
    const raw   = vals.reduce((a,b)=>a+b,0);
    const score = raw * DASS21.RAW_MULTIPLIER;
    const level = DASS21.cutoffs[g.key].find(c=>score<=c.max).lvl;
    out.subscales[g.key] = { key:g.key, label:g.label, full:g.full,
      raw, score, level, pct:Math.round(score/DASS21.SCORE_MAX*100),
      answered:7, missing:[] };
  });
  return out;
}

/* 分级 -> 配色档位。与既有 .pv3-s-fill green/amber 等类名保持一致。 */
function dassLevelTone(level){
  switch(level){
    case '正常':   return 'green';
    case '轻度':   return 'green';
    case '中度':   return 'amber';
    case '重度':   return 'red';
    case '极重度': return 'red';
    default:       return 'gray';
  }
}

const PANELS = {

/* ================= 心理综合评估 ================= */
psy: {
  icon:'psy', title:'心理综合评估', sub:'PSYCHOLOGICAL COMPREHENSIVE ASSESSMENT',
  /* ------------------------------------------------------------------
     v3 版本（2026-08-12 改造）
     来源：设计稿《心理综合评估 v3.html》
     方案 A：剥离设计稿自带顶栏(56px)与左导航(76px)，只取主内容区，
             嵌入本外壳弹窗 .p-body。样式见 static/psy_v3.css
     数据：全部沿用设计稿示例值，待接入 /get_openface /get_hr
     ------------------------------------------------------------------ */
  render: () => `
  <div class="psy-v3">

    <!-- ===== Layer 1 · 结论通栏 ===== -->
    <div class="pv3-card pv3-summary">
      <div class="pv3-score-block">
        <div class="pv3-score-ring">
          <svg viewBox="0 0 100 100" style="transform:rotate(-90deg);">
            <defs>
              <linearGradient id="pv3RingG" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#00E1A6"/>
                <stop offset="100%" stop-color="#00F7FF"/>
              </linearGradient>
            </defs>
            <circle cx="50" cy="50" r="42" fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="6"/>
            <!-- 综合评分（2026-08-12 占位化）：原为 81 分，进度环
                 stroke-dashoffset="50.1"（≈263.9×19%）与之对应。
                 这个分数没有任何计算依据 —— 五维/加权公式尚未确定
                 （设计稿并不存在），LLM 也未接入。一个居中的大号
                 「81 / 100」是整个面板里最像结论的元素，必须占位。
                 环形进度同步清零：只改数字不改环，会出现「显示 —
                 但环走了 81%」的自相矛盾。 -->
            <circle cx="50" cy="50" r="42" fill="none" stroke="url(#pv3RingG)" stroke-width="6"
                    stroke-linecap="round" stroke-dasharray="263.9" stroke-dashoffset="263.9"
                    data-score-ring
                    style="filter:drop-shadow(0 0 6px rgba(0,247,255,0.7));"/>
          </svg>
          <div class="num"><div class="n" data-score-num>—</div><div class="d">/ 100</div></div>
        </div>
        <div class="pv3-score-meta">
          <div class="label">综合心理状态</div>
          <!-- 「状态良好」「情绪稳定」「低压力」三处同样是未经测量的
               定性结论，与评分同源，一并占位。 -->
          <div class="title pv3-placeholder" data-score-title>待评估</div>
          <div class="pv3-tag-row" data-score-tags>
            <span class="pv3-tag gray">综合评分待计算</span>
          </div>
        </div>
      </div>

      <!-- ===== 核心结论（2026-08-12 占位化）=====
           原文案为设计稿示例：「情绪稳定性与放松度均高于常模基线」
           「未观察到持续性负向情绪或明显焦虑、抑郁倾向」「疲劳指数
           处于中等区间」。这些都是【结论性断言】：其一我们并没有常模
           数据，无从谈「高于基线」；其二把「没有测量」写成「未观察到
           抑郁倾向」，等于给出了一个阴性结论 —— 阴性结论比缺失结论
           更容易被当真。

           2026-08-13 起本栏已由 portrait_narrate.py 的规则叙述层填充
           （不走 LLM，纯规则，只描述已测得的数字）。下面的文案
           【保留】为回落态：三项采集未齐备、或 narrative 缺失时显示。
           文案不要再写「尚未接入」—— 能力已具备，缺的是采集数据，
           说成「未接入」会让用户去查配置而不是去完成采集。 -->
      <div class="pv3-conclusion">
        <div class="kicker">核心结论</div>
        <p data-conclusion-body class="pv3-placeholder">综合结论待三项采集（面部与视线、语音、量表）齐备后生成。下方各项为实际采集到的原始指标。</p>
      </div>

      <!-- 主导情绪：改由 emotion_view.js 从 /get_openface 填充。
           原写死「平静 / 72% / 置信度 77% / 持续 01:24」全为示例值，
           其中「持续 01:24」尤其具有欺骗性 —— 它暗示系统已经连续
           观测了 84 秒并得出稳定判断。
           「置信度」改称「稳定度」：该位置实际对应后端 emo_stability，
           它是情绪波动的稳定程度，不是分类置信度，原标签是错的。 -->
      <div class="pv3-dominant" data-emo-dominant>
        <div class="pv3-de-label">主导情绪</div>
        <div class="pv3-de-row">
          <span class="pv3-de-name">—</span>
          <span class="pv3-de-pct">—</span>
        </div>
        <div class="pv3-de-meta">
          <span>稳定度<b>—</b></span>
          <span>持续<b>—</b></span>
        </div>
      </div>
    </div>

    <!-- ===== 左右分栏 66 : 34 ===== -->
    <div class="pv3-split">

      <!-- ---------- 左：被动式感知（无感采集） ---------- -->
      <div class="pv3-col-out">
        <div class="pv3-col-label">
          <span>PASSIVE · 被动式感知</span>
          <span class="line"></span>
          <span>摄像头 · 无感采集</span>
        </div>

        <!-- Layer 2 -->
        <div class="pv3-layer-2">

          <!-- 五维心理画像 -->
          <div class="pv3-card">
            <div class="pv3-card-title">
              <h3>五维心理画像</h3>
              <!-- 原为「对比常模基线」：我们并无常模数据，该标注不成立 -->
              <span class="pv3-chip gray" data-portrait-chip>待采集</span>
            </div>
            <!-- ===== 五维心理画像（2026-08-12 占位化）=====
                 原实现为 5 条写死分数（情绪稳定86/放松度88/专注度79/
                 活力值72/压力值33）并配「常模基线」对比条。两处问题：
                   1) 分数无计算依据 —— 五维加权公式尚未定义（设计稿
                      并不存在，已向用户确认），这些是设计稿示例值。
                   2) 「常模基线」条更不能留：我们没有任何常模数据，
                      画出一条基线并显示「本次高于基线」是凭空对比。
                 维度名与 f(...) 公式说明保留：它们是后续定义公式的
                 输入依据，且不构成对用户的结论性断言。
                 待公式确定后，把 score/width 换成实测值即可。 -->
            <div class="pv3-portrait-list" data-portrait-list>
${[
  /* ===== 公式输入说明（2026-08-13 修正）=====
     原 5 条 tooltip 引用了三个【已证实恒为死值】的指标，鼠标悬停
     等于向使用者承诺我们给不出的能力：
       HRV-SDNN  —— HRV 通道已决定跳过（FPS 15 下 RMSSD 量化误差过大），
                    系统里根本没有这个量。
       PERCLOS   —— flask_openface_patch.py 里 ear_l/ear_r 硬编码 0.30，
                    0.30 > 0.20 恒真，PERCLOS 实测恒为 0.000。
       眨眼频率  —— 同上，恒为 0.0/min。
     现全部换成 portrait_state.py 快照里【真实存在】的字段名。
     公式权重仍未定稿，故文案统一写「= f(...)」而不给系数。 */
  /* 2026-08-13 批次 3b：公式已定稿，权重写进 tooltip。
     「AU 强度波动」已从情绪稳定里去掉 —— 实现时发现它没有数据源
     （快照里的 au_intensity 是单帧瞬时值，算不出窗口内波动），
     其权重已并入 emo_stability。详见 GET /portrait/formula_spec。
     第三列是维度 id，必须与后端 portrait_score.DIMENSION_ORDER 一致，
     渲染时靠它做 data-dim 锚点定位。 */
  ['情绪稳定','情绪稳定 = 0.70×emo_stability 情绪稳定度 + 0.30×主导情绪持续占比','emotion_stability'],
  ['放松度','放松度 = 0.45×心率 + 0.25×呼吸率 + 0.30×(1−AU04 皱眉强度) · 心率缺测时整项不计算','relaxation'],
  ['专注度','专注度 = 0.60×gaze_stability 注视稳定性 + 0.40×(1−pose_deviation_60s 头姿偏移占比)','focus'],
  ['活力值','活力值 = 0.40×F0 半音标准差 + 0.35×正向情绪占比 + 0.25×RMS 变异系数 · 探索性指标，不计入综合分','vitality'],
  ['压力值','压力值 = 0.50×DASS-21 压力分量表 level_norm + 0.30×心率 + 0.20×AU04 皱眉强度 · 数值越低越好','stress']
].map(([name,formula,dim])=>`
              <div class="pv3-p-row" data-dim="${dim}">
                <div class="pv3-p-name">${name}
                  <span class="info" title="${formula}">i</span>
                </div>
                <div class="pv3-p-bar-wrap">
                  <div class="pv3-p-bar-bg"></div>
                  <div class="pv3-p-fill" style="width:0%;"></div>
                </div>
                <div class="pv3-p-score dim" data-dim-score>—</div>
              </div>`).join('')}
            </div>
            <!-- ===== 采集完成度（2026-08-13 新增，接 /portrait/readiness）=====
                 五维画像需要面部 / 语音 / 量表三份数据【同时】在手，
                 而改造前这三份数据并不共存：
                   面部  60s 滚动窗口（deque maxlen=900），更早的帧被淘汰；
                   量表  答案只存在浏览器模块级变量，刷新即丢，后端从未见过；
                   语音  本来就是快照式，唯一不丢的一份。
                 所以「答完量表再去录音」这个自然流程拿不到完整数据。
                 后端 portrait_state.py 固化三份快照，readiness() 做齐备判定。

                 为什么做成【卡内横向条】而不是独立卡片：
                   pv3-layer-2 是 52fr/48fr 两列网格，第三个卡片会换行到
                   第二行并把锁定的高度预算顶开（根容器 overflow:hidden，
                   被顶出的部分直接消失而非产生滚动条）。
                   且完成度本就是这张卡的门控条件，放在卡内更贴语义。

                 三项各自独立完成、缺项写明缺什么，不做强制线性向导 ——
                 三项之间本无先后依赖，向导会把用户推着走。
                 渲染逻辑在 static/portrait_view.js。 -->
            <div class="pv3-ready-strip">
              <div class="pv3-ck-list" data-ready-list></div>
              <div class="pv3-ready-bar">
                <span class="pv3-chip gray" data-ready-chip>正在读取…</span>
                <button type="button" class="pv3-ready-btn" data-portrait-face>固化面部数据</button>
                <span class="rt" data-ready-hr>心率未测得</span>
              </div>
              <div class="pv3-ready-note" data-ready-note></div>
            </div>

            <!-- 图例同步修正：基线条已移除（无常模数据），若继续标注
                 「常模基线」会指向一个不存在的图形元素。 -->
            <div class="pv3-legend-row">
              <span class="li"><i class="pv3-sw cy"></i>本次测评</span>
              <span class="li"><i class="pv3-sw am"></i>反向指标<span class="pv3-p-tag-rev">越低越好</span></span>
              <span class="pv3-formula" style="margin-left:auto;" data-portrait-formula>需三项采集齐备</span>
            </div>
          </div>

          <!-- ===== 情绪构成 · 接入 /get_openface（2026-08-12 改造）=====
               原实现是设计稿写死的 6 条百分比（平静72/愉快15/悲伤6/
               惊讶4/愤怒2/恐惧1），看起来像真实测量结果，实为示例值。

               三处修正：
                 1) 数据源改为 GET /get_openface 的 emo_distribution，
                    由 emotion_view.js 每 2s 轮询渲染。
                 2) 类别数 6 → 8：后端 openface_parser.EMO_LABELS 有 8 类
                    （AffectNet-8），原前端漏掉了「厌恶」与「轻蔑」，
                    这正是历史上「应该有8种情绪分布但页面显示不全」的
                    同类问题。
                 3) 顺序沿用后端固定顺序，前端不再排序 —— 后端注释明确
                    要求固定顺序以保证横条位置稳定。
               未采集 / 无人脸 / 会话失效时显示占位说明，绝不显示百分比。 -->
          <div class="pv3-card">
            <div class="pv3-card-title">
              <h3>情绪构成</h3>
              <span class="pv3-chip gray" data-emo-chip>待采集</span>
            </div>
            <div class="pv3-emo-list" data-emo-list></div>
          </div>

        </div><!-- /layer-2 -->

        <!-- Layer 3 -->
        <div class="pv3-layer-3">

          <!-- ===== 心理量表估算 =====
               改造（2026-08-12）：抑郁/焦虑/压力三项改由下方 DASS-21 自评
               实际作答结果驱动（同一份问卷一次作答即拆出三分），
               不再写死示例值，也不再标注为「PSS/GAD-7/PHQ-9 等效估算」——
               那三个量表不在权威文档中，且与 DASS-21 的构念重复。
               未作答时显示占位符，绝不用示例数字冒充真实结果。
               疲劳指数仍来自 PERCLOS + 眨眼频率（视觉通道），保持原样。 -->
          <div class="pv3-card">
            <div class="pv3-card-title">
              <h3>心理量表估算</h3>
              <span class="pv3-chip gray" data-est-src>DASS-21 自评 · 待作答</span>
            </div>
            <div class="pv3-screen-list" data-est-list>

              ${['D','A','S'].map(k=>{
                const g = DASS21.groups[k];
                return `
              <div class="pv3-s-row" data-est-row="${k}">
                <div class="pv3-s-name">
                  <span class="k">${g.label}${k==='S'?'水平':'倾向'}</span>
                  <span class="d">DASS-21 · ${g.key} 分量表</span>
                </div>
                <div class="pv3-s-bar"><div class="pv3-s-fill gray" style="width:0%;"></div></div>
                <div class="pv3-s-val"><span class="n">—</span><span class="lvl gray">未答</span></div>
              </div>`;
              }).join('')}

              <!-- 疲劳指数（2026-08-12 占位化）：原写死 41 / 「中」，
                   并配 41% 的条宽，看上去是一个已完成的测量。
                   后端 /get_openface 确有 fatigue / perclos / blink_rate，
                   但当前面板并未消费它们；在真正接线之前显示 41 属于
                   凭空数字。待接 fatigue 字段后再恢复为实测值。 -->
              <div class="pv3-s-row" data-fatigue-row>
                <div class="pv3-s-name">
                  <span class="k">疲劳指数</span>
                  <span class="d">PERCLOS + 眨眼频率</span>
                </div>
                <div class="pv3-s-bar"><div class="pv3-s-fill gray" style="width:0%;"></div></div>
                <div class="pv3-s-val"><span class="n">—</span><span class="lvl gray">未测</span></div>
              </div>

            </div>
            <div class="pv3-disclaimer">抑郁 / 焦虑 / 压力三项为 DASS-21 自评量表得分（原始分 ×2）；疲劳指数为行为信号估算值。本结果非临床诊断依据，如有持续不适请咨询专业人员。</div>
          </div>

          <!-- ===== 注意与表情行为（2026-08-13 替换原「情绪时间线」）=====
               原情绪时间线是一条写死的贝塞尔 SVG 曲线，没有任何数据源，
               三条线（平静/愉快/负向）的坐标是手写常量 —— 它看起来像实时图表，
               实际上无论用户什么状态都长一个样，属于编造数据，故整块移除。

               本块把首页的「头姿 & 视线」与「面部表情 & Action Units」两卡融合。
               栏目名取「注意与表情行为」而非「眼动追踪分析」：
                 - 模型只有 gaze_yaw/gaze_pitch 两个角度，没有瞳孔、没有眼睑关键点、
                   没有屏幕标定，做不到真正的"眼动追踪"（注视点/扫视轨迹/热力图）。
                   叫「眼动追踪」会承诺一个我们给不出的能力。
                 - 「注意」对应视线朝向与注视稳定性，「表情行为」对应 AU 强度，
                   两者都是心理评估里的标准可观察行为指标（非自评、客观旁证），
                   与左侧 DASS-21 自评形成"自评 + 客观行为"的互补，命名也贴合语境。

               数据全部来自 /get_openface 的真实字段：
                 pose.yaw / pose.pitch      ← MLT 模型 gaze 回归头（真实）
                 gaze_state                 ← 由 yaw/pitch 按 ±15° 阈值判定（真实）
                 gaze_stability             ← 60s 窗口 exp(-var/200)（真实）
                 pose_deviation_60s         ← 60s 内"偏移"帧占比（真实）
                 au_intensity               ← MLT 模型 AU 回归头 8 维（真实）
                 au_dominant                ← 最强 AU 的中文名（真实）
               已核实为占位/恒定值、因此本块【不予展示】的字段：
                 blink_rate / perclos / ear_l / ear_r
                   ← flask_openface_patch.py 里 ear 恒为 0.30，0.30>0.20 恒真，
                     导致 PERCLOS 恒为 0.000、眨眼恒为 0.0/min。是死值，不是数据。
                 pose.roll                  ← 源码注释明确写 "CSV 里没 roll，占位 0"
                 au_symmetry                ← 硬编码 0.96
                 au_activity                ← 阈值写成 >1.0，但 AU 输出上限约 0.6，恒为 0%
               这些字段一旦显示出来就是"看起来合理的假数字"，比缺失更危险。 -->
          <div class="pv3-card pv3-beh-card">
            <div class="pv3-card-title">
              <h3>注意与表情行为</h3>
              <span class="pv3-chip gray" data-beh-src>客观行为 · 实时</span>
            </div>
            <div class="pv3-beh-body">

              <!-- 左：注意朝向罗盘。准心 = 正视，圆点 = 当前视线落点。
                   yaw/pitch 各 ±30° 线性映射到罗盘半径，与首页同一映射规则。 -->
              <div class="pv3-beh-gaze">
                <div class="pv3-gaze-compass">
                  <div class="ring outer"></div>
                  <div class="ring inner"></div>
                  <div class="cross h"></div>
                  <div class="cross v"></div>
                  <div class="gdot" data-beh-dot style="transform:translate(-50%,-50%);"></div>
                </div>
                <div class="pv3-gaze-kv">
                  <div class="st" data-beh-state>等待检测…</div>
                  <div class="kv"><span class="k">Yaw</span><span class="v" data-beh-yaw>—</span></div>
                  <div class="kv"><span class="k">Pitch</span><span class="v" data-beh-pitch>—</span></div>
                  <div class="kv"><span class="k">注视稳定性</span><span class="v" data-beh-stab>—</span></div>
                  <div class="kv"><span class="k">60s 偏移</span><span class="v" data-beh-dev>—</span></div>
                </div>
              </div>

              <!-- 右：表情动作单元强度。6 项对应模型真实输出的 AU，
                   AU01/AU02 合并为「眉毛」取均值（与首页一致）。
                   横条按 sqrt(v/cap) 映射：AU 日常波动多在 0~0.1，
                   线性映射下几乎看不出差异。 -->
              <div class="pv3-beh-au">
                <div class="pv3-au-head">
                  <span class="lb">主导表情动作</span>
                  <span class="dm" data-beh-audom>—</span>
                </div>
                <div class="pv3-au-list" data-beh-aulist>
                  ${[
                    ['AU12','嘴角上扬',0.3],
                    ['AU06','脸颊上抬',0.3],
                    ['AU04','皱眉',0.3],
                    ['AU25','双唇分开',0.6],
                    ['AU09','皱鼻',0.3],
                    ['BROW','眉毛',0.3]
                  ].map(([code,cn,cap])=>`
                    <div class="pv3-au-row" data-au-code="${code}" data-au-cap="${cap}">
                      <span class="nm">${cn}</span>
                      <span class="tr"><i class="fl" data-au-fill></i></span>
                      <span class="vl" data-au-val>0.00</span>
                    </div>
                  `).join('')}
                </div>
              </div>

            </div>
            <div class="pv3-beh-foot">
              <span data-beh-note>视线朝向与表情动作由摄像头逐帧解析，非自评数据</span>
              <span class="rt" data-beh-win>—</span>
            </div>
          </div>

        </div><!-- /layer-3 -->

        <!-- AI 综合结论 -->
        <div class="pv3-ai-row">
          <div class="pv3-card pv3-ai-card">
            <div class="pv3-card-title">
              <h3>AI 综合解读</h3>
              <!-- CTA 初始仍为 disabled，但不再是永久状态：
                   ai_view.js 会先探 /portrait/ai_status，凭证就绪才解禁。
                   保持"默认灰、探测后解禁"的方向，是因为反过来做
                   （默认可点、失败再灰）会让没配凭证的部署出现
                   一个点了就报错的主按钮。 -->
              <div class="pv3-ai-cta disabled" data-ai-cta aria-disabled="true">
                <span class="ic"><svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg></span>
                <span class="lb">生成解读</span>
              </div>
              <!-- 报告单入口（2026-08-21）。
                   与「生成解读」并列而非替代：解读是这一栏的内容，
                   报告是整份测评的汇总输出，两者不是一回事。
                   报告【永不置灰】—— 按拍板结果，数据不齐备也允许生成，
                   报告内部会显著标注缺什么。置灰会让用户既看不到报告、
                   也不知道差什么。 -->
              <div class="pv3-ai-cta pv3-rpt-cta" data-rpt-cta
                   title="在新标签页打开报告单，可打印或另存为 PDF">
                <span class="ic"><svg viewBox="0 0 24 24"><path d="M7 3h7l5 5v13H7zM14 3v5h5M9.5 13h6M9.5 16.5h6"/></svg></span>
                <span class="lb">生成报告</span>
              </div>
            </div>
            <!-- ===== AI 综合解读 =====
                 2026-08-12 占位化的原因（保留备忘，避免有人"好心"改回去）：
                 原文案通篇是编造的结论与数字 —— 情绪「平静 72%」、
                 「疲劳指数处于中等区间（41）」、「高于常模基线」，
                 还给出「每工作 45min 起身 3-5min」「4-7-8 呼吸放松法」
                 这类处置建议。它不由任何模型生成，却排版成「本次测评显示…」。

                 2026-08-21 接入真实模型（DashScope / qwen-plus）。
                 下面的占位文案【保留】，作为失败回落态：
                 凭证缺失、接口超时、采集不足时，ai_view.js 会退回这里，
                 而不是显示假内容。真实解读由 ai_view.js 注入替换。

                 注意首句不要再写「尚未接入/缺少凭据」：凭证现已接入，
                 那句话会把「采集不足」「接口超时」一律误导成「功能没做」，
                 用户会去查配置而不是去补采集。回落文案必须对原因保持中性，
                 真实原因由 ai_view.js 在 reason 里具体说明。 -->
            <div class="pv3-ai-body" data-ai-body>
              <p class="pv3-placeholder">三项采集（面部 / 语音 / 量表）全部完成后，将<b>自动生成</b>解读，基于本次<b>已实际测得</b>的指标做客观归纳；未测量的维度不会被推断。也可点击右上角按钮立即生成或重新生成。</p>
              <p class="pv3-placeholder">当前面板中「情绪构成」「主导情绪」「语音任务」「心理量表自评」为实际采集/作答结果，其余标记为「—」的条目表示尚未测量。</p>
            </div>
          </div>
        </div>

      </div><!-- /col-out -->

      <!-- ---------- 右：主动式交互（需用户参与） ---------- -->
      <div class="pv3-col-in">
        <div class="pv3-col-label">
          <span class="dot"></span>
          <span>ACTIVE · 主动式交互</span>
          <span class="line"></span>
          <span>需用户参与</span>
        </div>

        <!-- ===== 语音任务 · 真实录音（2026-08-12 接入）=====
             改造要点（与后端 audio_client.TASK_SPEC 对齐，不再硬编码）：
               1) 阶段/提示语/文本/时长全部来自 GET /audio/task_spec，
                  前端不写死。此前的示例文本「清晨的湖面…」已移除。
               2) 采样率标注改为动态：原写死「16kHz」是错的，
                  后端 task_spec.sample_rate 明确为 48000。
               3) 移除「自由叙述」页签：后端 disabled_stages 已禁用该段
                  （自由叙述下 F0 方差跨用户不可比），留着等于给出
                  一个点不动的入口。页签改为按 task_spec 的阶段渲染。
               4) 原「本地处理 · 不上传原始音频」是【错误表述】：
                  实现是把 WAV 上传到 /audio/upload 再转发微服务分析。
                  已改为如实说明，避免对用户做出不成立的承诺。
               5) 波形/计时不再是 Math.sin 画的静态装饰与写死的 00:42，
                  改由 voice_recorder.js 驱动真实数据。
             交互契约：元音段锁 5s 到点自停；朗读段 duration_mode=
             until_user_done，不倒计时，读完由用户点按钮结束。 -->
        <div class="pv3-card pv3-voice-card">
          <div class="pv3-card-title">
            <h3>语音任务</h3>
            <div class="pv3-voice-tabs" data-vt-tabs>
              <span class="t" data-vt-tab="vowel">元音</span>
              <span class="t" data-vt-tab="reading">朗读</span>
            </div>
          </div>

          <div class="pv3-voice-prompt">
            <div class="lbl" data-vt-lbl>正在加载任务…</div>
            <div class="text" data-vt-text></div>
          </div>

          <!-- 波形居中、控件横向一行（2026-08-13 布局调整）
               原结构是「波形 / 计时 / 按钮」三层纵向堆叠，占掉约 130px 高度，
               把朗读文本区压得只剩两三行。现改为：
                 上：声纹示意（波形）
                 下：计时 · 录音按钮 · 状态文字  —— 三者横向分布
               纵向层数由 3 减到 2，省下的高度全部让给朗读文本区。
               注意：data-vt-* 钩子与 .bar 结构必须原样保留 ——
               voice_recorder.js 依赖 13 个 data-vt-* 与 wave 下的 .bar 列表，
               容器可以改，钩子改了录音会静默失效（不报错，按钮点了没反应）。 -->
          <div class="pv3-voice-body">
            <div class="pv3-voice-wave" data-vt-wave>
              ${Array.from({length:64}, ()=>
                `<div class="bar" style="height:3px;opacity:0.16;"></div>`
              ).join('')}
            </div>
            <div class="pv3-voice-ctrl">
              <div class="pv3-voice-timer">
                <span class="cur" data-vt-cur>00:00</span><span class="tot" data-vt-tot></span>
              </div>
              <div class="pv3-mic-btn disabled" data-vt-mic title="正在初始化…">
                <svg viewBox="0 0 24 24">
                  <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/>
                  <path d="M5 11v1a7 7 0 0 0 14 0v-1"/>
                  <path d="M12 19v3"/>
                </svg>
              </div>
              <div class="pv3-mic-side">
                <div><span class="mode" data-vt-mode>待录音</span> · 采样 <span data-vt-sr>—</span></div>
                <div data-vt-f0>基频 F0 · —</div>
              </div>
            </div>
          </div>

          <div class="pv3-vt-status" data-vt-status>正在初始化…</div>
          <div class="pv3-vt-metrics" data-vt-metrics></div>

          <div class="pv3-voice-hint">
            <span>提取维度：<b>语速</b> · <b>基频</b> · <b>抖动</b> · <b>停顿</b></span>
            <span>音频上传至本机服务分析</span>
          </div>
        </div>

        <!-- ===== 心理量表自评 · DASS-21 直接作答 =====
             改造（2026-08-12）：原先是 4 个量表的入口列表（PSS-10 / GAD-7 /
             PHQ-9 / PSQI），需点进子页面才能答题，且这 4 个量表均不在
             用户提供的权威文档中。现改为把 DASS-21 的 21 道题直接铺开，
             就地点选即可作答，省掉「进入子页面」这一跳。
             作答结果实时拆成 抑郁/焦虑/压力 三分，回填上方「心理量表估算」。 -->
        <div class="pv3-card pv3-dass-card">
          <div class="pv3-card-title">
            <h3>心理量表自评</h3>
            <span class="pv3-chip" data-dass-progress>0 / 21 已作答</span>
          </div>

          <!-- [2026-08-13] 单题模式。
               改动 1：移除原 .pv3-dass-head（量表名称「DASS-21 抑郁-焦虑-压力量表」
                       + 选项图例）。卡片标题已写「心理量表自评」，底部得分区也标注了
                       抑郁/焦虑/压力三维，量表名属重复信息；选项图例在每题的
                       4 个按钮上已有文字，无需再列一遍。
               改动 2：21 题一次全铺 → 一次只显示当前 1 题，答完自动进入下一题。
                       原来 21 题密集堆叠，需要长时间滚动，视觉负担重；
                       单题模式下卡片高度需求从「越高越好」变成固定一题的高度。
               实现：DOM 仍然一次性生成 21 个 item（保持 paint() 的回填逻辑与
               事件委托不变，改动面最小），由 CSS + .cur 类控制只显示当前那一题。
               这样做而不是每次重建 DOM 的原因：重建会丢失已绑定的事件委托并
               增加状态同步出错的机会，而显示控制是纯样式问题。 -->
          <div class="pv3-dass-list pv3-dass-single" data-dass-list>
            ${DASS21.items.map(it=>`
              <div class="pv3-dass-item" data-dass-item="${it.n}">
                <div class="pv3-dass-q">
                  <span class="idx">${it.n}</span>
                  <span class="txt">${it.text}</span>
                </div>
                <div class="pv3-dass-opts">
                  ${DASS21.options.map(o=>`
                    <button type="button" class="opt" data-dass-set="${it.n}" data-dass-val="${o.v}"
                            title="${o.v} · ${o.label}"><i>${o.v}</i><span>${o.short}</span></button>
                  `).join('')}
                </div>
              </div>
            `).join('')}
          </div>

          <!-- 单题模式下的导航：上一题 / 题号进度 / 下一题。
               必须提供「上一题」——自动前进如果不能回退，误点就无法修正，
               只能清空重答 21 题，这是不可接受的。 -->
          <div class="pv3-dass-nav">
            <button type="button" class="nv" data-dass-prev>‹ 上一题</button>
            <span class="pos"><b data-dass-pos>1</b> / ${DASS21.items.length}</span>
            <button type="button" class="nv" data-dass-next>下一题 ›</button>
          </div>

          <!-- 实时得分。三个分量表各自独立出分：某一维 7 题答满即出该维分数，
               不必等 21 题全答完。缺项的维度显示进度而非分数。 -->
          <div class="pv3-dass-foot">
            <div class="pv3-dass-scores" data-dass-scores></div>
            <div class="pv3-dass-actions">
              <span class="pv3-dass-note" data-dass-note>各维答满 7 题即出分（原始分 ×2）</span>
              <button type="button" class="pv3-dass-reset" data-dass-reset>清空重答</button>
            </div>
          </div>
        </div>

      </div><!-- /col-in -->

    </div><!-- /split -->

    <!-- ===== 底部信息条 ===== -->
    <div class="pv3-footer">
      <span>多模态估算模型 <span class="v">v2.4.1</span></span>
      <i class="dot"></i>
      <span>结果仅供参考，不构成医疗建议</span>
      <i class="dot"></i>
      <span>Session · <span class="v">2026-08-12 14:47:22 UTC+8</span></span>
    </div>

  </div>
  `,

  /* ------------------------------------------------------------------
     面板挂载后的初始化。由外壳 buildPopup() 在插入 DOM 后调用。
     必须在这里绑定而不是用内联 onclick：内联处理器要求函数挂到
     window 全局，而本文件的常量都是模块级 const，取不到。

     每次打开面板都会重建 DOM，所以这里同时负责把 DASS21_STATE 里
     已有的答案回填到新 DOM 上 —— 否则关闭再打开，答过的题会看起来
     像没答过（数据其实还在，只是没渲染出来，属于最容易漏的一类 bug）。
     ------------------------------------------------------------------ */
  mount: (root) => {
    /* 语音任务录音（voice_recorder.js）。
       必须放在下面 DASS 的 early-return【之前】：若 DASS 结构缺失导致
       提前 return，录音初始化会被一并跳过，表现为「麦克风按钮永远
       停在初始化中」这种极难定位的现象。
       两者互不依赖，因此各自独立 try —— 一个坏了不影响另一个。 */
    try {
      if (window.VoiceRecorder && window.VoiceRecorder.mount) {
        window.VoiceRecorder.mount(root);
      }
    } catch (e) {
      console.error('[psy] 语音任务初始化失败:', e);
    }

    /* 情绪构成 / 主导情绪（emotion_view.js）。
       同理必须放在 DASS early-return 之前，且独立 try：否则 DASS 结构
       缺失时情绪卡会永远停在「正在读取…」。 */
    try {
      if (window.EmotionView && window.EmotionView.mount) {
        window.EmotionView.mount(root);
      }
    } catch (e) {
      console.error('[psy] 情绪构成初始化失败:', e);
    }

    /* 注意与表情行为（behavior_view.js）。独立 try 的理由同上：
       这是旁路的客观行为展示，它挂不上不能影响 DASS-21 自评作答。 */
    try {
      if (window.BehaviorView && window.BehaviorView.mount) {
        window.BehaviorView.mount(root);
      }
    } catch (e) {
      console.error('[psy] 注意与表情行为初始化失败:', e);
    }

    /* 采集完成度清单（portrait_view.js）。同样必须在 DASS early-return
       【之前】且独立 try：它是三项数据的唯一进度出口，DASS 结构缺失
       不能让用户失去「还缺什么」的可见性。
       它还向本文件暴露 _submitScale / _resetScale 两个钩子，
       下方 DASS 作答逻辑依赖它们把答案送到后端。 */
    try {
      if (window.PortraitView && window.PortraitView.mount) {
        window.PortraitView.mount(root);
      }
    } catch (e) {
      console.error('[psy] 采集完成度初始化失败:', e);
    }

    /* AI 综合解读（ai_view.js）。同样必须在 DASS early-return【之前】
       且独立 try。

       2026-08-21 补接：此前本模块【漏了】这一句，是「生成解读」按钮
       点不动的根因。ai_view.js 原先靠自己 setInterval 轮询等 DOM 出现，
       但外壳 openPopup() 每次打开面板都重建 DOM，而那个轮询只在页面
       load 后跑 30 秒 —— 用户 30 秒内没点开面板，此后 CTA 就永久停在
       初始的 .disabled 上。DOM 的生命周期由这里掌握，绑定就必须在这里做。 */
    try {
      if (window.AiView && window.AiView.mount) {
        window.AiView.mount(root);
      }
    } catch (e) {
      console.error('[psy] AI 综合解读初始化失败:', e);
    }

    const listEl  = root.querySelector('[data-dass-list]');
    if (!listEl) return;                       // 结构变更时静默跳过，不报错阻断面板
    const progEl  = root.querySelector('[data-dass-progress]');
    const scoreEl = root.querySelector('[data-dass-scores]');
    const noteEl  = root.querySelector('[data-dass-note]');
    const srcEl   = root.querySelector('[data-est-src]');
    const estList = root.querySelector('[data-est-list]');
    const prevBtn = root.querySelector('[data-dass-prev]');
    const nextBtn = root.querySelector('[data-dass-next]');
    const posEl   = root.querySelector('[data-dass-pos]');

    /* 单题模式的「当前题」下标（0-based，指向 DASS21.items）。
       放在 mount 作用域而非模块级：它是纯视图状态，关闭面板后从
       第一个未作答题重新开始更符合直觉；答案本身仍在模块级
       DASS21_STATE 里，不会丢。 */
    let curIdx = (function firstUnanswered(){
      const i = DASS21.items.findIndex(it => DASS21_STATE.answers[it.n] === undefined);
      return i < 0 ? DASS21.items.length - 1 : i;   // 全答完则停在最后一题
    })();

    /* 后端返回的权威得分。null = 尚未提交或提交失败，此时回填退回
       前端即时计分（仅作过程反馈）。 */
    let srvScored = null;
    let submitting = false;

    /* 21 题答满时把答案送到后端固化。
       为什么必须送：DASS21_STATE 是【浏览器模块级变量】，刷新页面
       21 题全丢，后端从未见过这份数据 —— 而五维画像在后端计算，
       它需要量表分与面部、语音三份数据同时在手。
       只在「答满 21 题」时提交，不做逐题上报：逐题会产生 21 次请求，
       且中途的部分答案对后端毫无用处（缺项不求和）。 */
    function trySubmit(){
      if (!DASS21_STATE.isComplete) return;
      if (submitting) return;
      if (!(window.PortraitView && window.PortraitView._submitScale)) return;
      submitting = true;
      const answers = Object.assign({}, DASS21_STATE.answers);
      window.PortraitView._submitScale(answers, scoreDASS21(answers))
        .then(j=>{
          submitting = false;
          if (j && j.status === 'ok'){
            srvScored = j.scored || null;
            /* client_agrees === false 意味着前后端算出的原始分不一致，
               这是契约级异常（同一份答案两套实现算出两个结果），
               必须让它可见，不能静默采用某一边。 */
            if (j.client_agrees === false){
              console.error('[psy] DASS 前后端计分不一致，已采用后端结果', j.scored);
            }
            paint();
          }
        })
        .catch(()=>{ submitting = false; });
    }

    /* 把状态渲染到 DOM。answers 是唯一数据源，DOM 只是它的投影。 */
    function paint(){
      const A = DASS21_STATE.answers;

      // 1) 各题选中态 + 单题模式下的「当前题」显示控制
      curIdx = Math.max(0, Math.min(curIdx, DASS21.items.length - 1));  // 夹取，防越界
      DASS21.items.forEach((it, i)=>{
        const row = listEl.querySelector(`[data-dass-item="${it.n}"]`);
        if (!row) return;
        const picked = A[it.n];
        row.classList.toggle('answered', picked !== undefined);
        row.classList.toggle('cur', i === curIdx);
        row.querySelectorAll('[data-dass-val]').forEach(b=>{
          b.classList.toggle('on', Number(b.dataset.dassVal) === picked);
        });
      });

      // 1b) 导航状态
      if (posEl) posEl.textContent = String(curIdx + 1);
      if (prevBtn) prevBtn.disabled = (curIdx === 0);
      if (nextBtn) nextBtn.disabled = (curIdx === DASS21.items.length - 1);

      // 2) 进度
      const done = DASS21_STATE.answeredCount;
      if (progEl){
        progEl.textContent = `${done} / ${DASS21.items.length} 已作答`;
        progEl.classList.toggle('done', done === DASS21.items.length);
      }

      // 3) 三分量表得分
      const res = scoreDASS21(A);
      if (scoreEl){
        scoreEl.innerHTML = ['D','A','S'].map(k=>{
          const s = res.subscales[k];
          if (s.score === null){
            return `<div class="sc pending">
                      <span class="lb">${s.label}</span>
                      <span class="vv">${s.answered}/7</span>
                    </div>`;
          }
          return `<div class="sc ${dassLevelTone(s.level)}">
                    <span class="lb">${s.label}</span>
                    <span class="vv">${s.score}</span>
                    <span class="lv">${s.level}</span>
                  </div>`;
        }).join('');
      }
      if (noteEl){
        noteEl.textContent = res.complete
          ? '21 题已答完 · 三项得分均为最终值（原始分 ×2）'
          : '各维答满 7 题即出分（原始分 ×2）';
      }

      // 4) 回填上方「心理量表估算」
      /* 得分来源优先级：后端 srvScored > 前端 res。
         前端 scoreDASS21 只用于「答题过程中」的即时反馈（各维 7 题答满
         即出分），最终值一律以后端为准。原因是前端的 pct = score/42
         已实测出错：三个分量表的临床切点完全不同（焦虑「正常」上限 7
         -> 17%，压力「正常」上限 14 -> 33%），实测 score=12 同时对应
         「焦虑 中度」与「压力 正常」，两者都显示 29%。后端另给了
         level_norm（分级序号归一），那才是能跨分量表比较的量。 */
      if (estList){
        ['D','A','S'].forEach(k=>{
          const s   = (srvScored && srvScored.subscales && srvScored.subscales[k]
                       && srvScored.subscales[k].score !== null)
                        ? srvScored.subscales[k]
                        : res.subscales[k];
          const row = estList.querySelector(`[data-est-row="${k}"]`);
          if (!row) return;
          const fill = row.querySelector('.pv3-s-fill');
          const num  = row.querySelector('.pv3-s-val .n');
          const lvl  = row.querySelector('.pv3-s-val .lvl');
          if (s.score === null){
            fill.className = 'pv3-s-fill gray';
            fill.style.width = '0%';
            num.textContent = '—';
            lvl.className = 'lvl gray';
            lvl.textContent = s.answered ? `${s.answered}/7` : '未答';
          } else {
            const tone = dassLevelTone(s.level);
            fill.className = 'pv3-s-fill ' + tone;
            fill.style.width = s.pct + '%';
            num.textContent = s.score;
            lvl.className = 'lvl ' + tone;
            lvl.textContent = s.level;
          }
        });
      }
      if (srcEl){
        /* 三态而非两态：「答完」与「已固化到后端」是两件事。
           只显示「已完成」会让用户以为数据已经进入画像计算，
           而实际上提交可能失败（会话过期等）。 */
        srcEl.textContent = res.complete
          ? (srvScored ? 'DASS-21 自评 · 已完成并固化' : 'DASS-21 自评 · 已答完，正在提交…')
          : (done ? `DASS-21 自评 · ${done}/21 进行中` : 'DASS-21 自评 · 待作答');
      }
    }

    /* 事件委托：21 题 × 4 选项 = 84 个按钮，逐个绑定既慢又易漏。 */
    listEl.addEventListener('click', (e)=>{
      const btn = e.target.closest('[data-dass-set]');
      if (!btn) return;
      const n = Number(btn.dataset.dassSet);
      const v = Number(btn.dataset.dassVal);
      // 再点同一项 = 取消作答，避免误点后无法回到「未答」状态
      const isUndo = (DASS21_STATE.answers[n] === v);
      if (isUndo) delete DASS21_STATE.answers[n];
      else DASS21_STATE.answers[n] = v;

      /* 作答后自动进入下一题（取消作答时【不】前进 —— 用户正在修正，
         把他推走会让他找不回刚改的那题）。
         延时 180ms 而不是立刻跳：让选中态的高亮先被看到，
         否则点下去题目瞬间就换了，用户无法确认自己点中了哪一项。
         最后一题不前进，留在原处等「完成」。 */
      if (!isUndo && curIdx < DASS21.items.length - 1){
        paint();                       // 先把选中态画出来
        setTimeout(()=>{ curIdx += 1; paint(); }, 180);
        trySubmit();                   // 答满 21 题时才真正发出（内部自检）
        return;
      }
      paint();
      /* 取消作答会让「已答满」变回「未答满」。此时后端仍存着上一次
         提交的完整结果，若不清掉，界面会显示一份与当前作答不符的分数。 */
      if (isUndo && srvScored){
        srvScored = null;
        if (window.PortraitView && window.PortraitView._resetScale){
          window.PortraitView._resetScale();
        }
        paint();
      }
      trySubmit();
    });

    /* 上一题 / 下一题。允许跳过未作答的题（不强制作答才能前进）：
       强制会让用户卡在不想答的题上，只能清空重答。 */
    if (prevBtn) prevBtn.addEventListener('click', ()=>{
      if (curIdx > 0){ curIdx -= 1; paint(); }
    });
    if (nextBtn) nextBtn.addEventListener('click', ()=>{
      if (curIdx < DASS21.items.length - 1){ curIdx += 1; paint(); }
    });

    const resetBtn = root.querySelector('[data-dass-reset]');
    if (resetBtn) resetBtn.addEventListener('click', ()=>{
      if (DASS21_STATE.answeredCount === 0) return;
      DASS21_STATE.reset();
      srvScored = null;
      /* 同步清掉后端已固化的量表快照。不清的后果是前端显示「未答」
         而后端 readiness 仍报 scale.done=true —— 前后端状态分叉，
         用户会看到「量表已完成」却找不到自己的答案。 */
      if (window.PortraitView && window.PortraitView._resetScale){
        window.PortraitView._resetScale();
      }
      curIdx = 0;                      // 清空后回到第 1 题
      paint();
      listEl.scrollTop = 0;
    });

    paint();     // 首次渲染 + 重开面板时回填既有答案
    /* 重开面板的补提交：DASS21_STATE 跨面板开关存活，但后端快照可能
       因会话超时（30 分钟）被清掉，或上次提交恰好失败。
       trySubmit 自身幂等（未答满直接返回），这里无条件调一次。 */
    trySubmit();
  }
},

/* ================= 生理健康评估 ================= */
bio: {
  icon:'bio', title:'生理健康评估', sub:'PHYSIOLOGICAL HEALTH ASSESSMENT',
  render: () => `
    <div class="grid-2" style="margin-bottom:20px;">
      <div class="pcard hi">
        <h4>生理健康综合评分</h4>
        <div class="ring-box">
          <div class="ring">
            <svg viewBox="0 0 120 120">
              <circle cx="60" cy="60" r="50" fill="none" stroke="rgba(255,255,255,.06)" stroke-width="9"/>
              <circle cx="60" cy="60" r="50" fill="none" stroke="#00E1A6" stroke-width="9"
                stroke-linecap="round" stroke-dasharray="314" stroke-dashoffset="40"
                style="filter:drop-shadow(0 0 6px #00E1A6)"/>
            </svg>
            <div class="rv"><b>87</b><span>健康评分</span></div>
          </div>
          <div style="flex:1;">
            <div style="margin-bottom:10px;"><span class="tinytag g">心肺功能正常</span> <span class="tinytag o">呼吸率偏高</span></div>
            <div style="font-size:12px;color:var(--gray);line-height:1.9;">
              基于 rPPG 非接触式测量的多项生理指标综合评估，
              心血管指标处于<b style="color:var(--green)">正常范围</b>。
            </div>
          </div>
        </div>
      </div>

      <div class="pcard">
        <h4>核心生理指标</h4>
        <div class="kv"><span class="k">心率 HR</span><span class="v ok">78 bpm <span style="color:var(--gray-2);font-weight:normal;">(60~100)</span></span></div>
        <div class="kv"><span class="k">血氧 SpO₂</span><span class="v ok">97 % <span style="color:var(--gray-2);font-weight:normal;">(≥95)</span></span></div>
        <div class="kv"><span class="k">呼吸率 RR</span><span class="v warn">25.5 次/分 <span style="color:var(--gray-2);font-weight:normal;">(12~20)</span></span></div>
        <div class="kv"><span class="k">血压估计 BP</span><span class="v">118 / 76 mmHg</span></div>
        <div class="kv"><span class="k">HRV · SDNN</span><span class="v ok">52 ms</span></div>
        <div class="kv"><span class="k">HRV · RMSSD</span><span class="v ok">38 ms</span></div>
      </div>
    </div>

    <div class="section-title">rPPG 波形分析 <span class="aside">15 秒窗口</span></div>
    <div class="pcard" style="padding:20px;margin-bottom:20px;">
      <svg viewBox="0 0 1200 200" style="width:100%;height:180px;" preserveAspectRatio="none">
        <g stroke="rgba(0,247,255,.08)">
          <line x1="0" x2="1200" y1="30" y2="30"/><line x1="0" x2="1200" y1="75" y2="75"/>
          <line x1="0" x2="1200" y1="120" y2="120"/><line x1="0" x2="1200" y1="165" y2="165"/>
        </g>
        <path d="M0,100 Q20,30 40,100 Q60,170 80,100 Q100,50 120,100 Q140,160 160,100 Q180,30 200,100 Q220,170 240,100 Q260,40 280,100 Q300,165 320,100 Q340,35 360,100 Q380,170 400,100 Q420,45 440,100 Q460,160 480,100 Q500,30 520,100 Q540,170 560,100 Q580,50 600,100 Q620,165 640,100 Q660,40 680,100 Q700,170 720,100 Q740,35 760,100 Q780,165 800,100 Q820,45 840,100 Q860,170 880,100 Q900,30 920,100 Q940,165 960,100 Q980,45 1000,100 Q1020,170 1040,100 Q1060,35 1080,100 Q1100,165 1120,100 Q1140,45 1160,100 Q1180,170 1200,100" fill="none" stroke="#00E1A6" stroke-width="1.8"/>
        <path d="M0,135 Q100,125 200,132 T400,128 T600,135 T800,128 T1000,132 T1200,130" fill="none" stroke="#FFD466" stroke-width="1.5" opacity=".85"/>
      </svg>
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--gray-2);margin-top:6px;">
        <span>0s</span><span>3s</span><span>6s</span><span>9s</span><span>12s</span><span>15s</span>
      </div>
    </div>

    <div class="grid-3">
      <div class="pcard">
        <h4>心血管风险</h4>
        <div class="hbar"><div class="t"><span>心律不齐风险</span><b>低</b></div><div class="b"><i style="width:14%;background:#00E1A6;"></i></div></div>
        <div class="hbar"><div class="t"><span>血管弹性指数</span><b>良好</b></div><div class="b"><i style="width:78%;"></i></div></div>
        <div class="hbar"><div class="t"><span>心脏负荷</span><b>正常</b></div><div class="b"><i style="width:32%;background:#00E1A6;"></i></div></div>
        <div class="hbar"><div class="t"><span>房颤风险</span><b>未检出</b></div><div class="b"><i style="width:8%;background:#00E1A6;"></i></div></div>
      </div>

      <div class="pcard">
        <h4>自主神经平衡</h4>
        <svg width="100%" height="130" viewBox="0 0 220 130">
          <path d="M 30 105 A 80 80 0 0 1 190 105" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="14"/>
          <path d="M 30 105 A 80 80 0 0 1 140 32" fill="none" stroke="#00F7FF" stroke-width="14" stroke-linecap="round" style="filter:drop-shadow(0 0 6px #00F7FF)"/>
          <text x="110" y="88" text-anchor="middle" fill="#fff" font-size="26" font-weight="bold">62%</text>
          <text x="110" y="112" text-anchor="middle" fill="#9BFFFB" font-size="10" letter-spacing="1">交感/副交感均衡度</text>
        </svg>
        <div style="font-size:11px;color:var(--gray);text-align:center;margin-top:6px;">LF/HF = 1.8 · <span style="color:var(--yellow);">轻度交感偏向</span></div>
      </div>

      <div class="pcard">
        <h4>体征趋势（24h）</h4>
        <svg width="100%" height="130" viewBox="0 0 220 130" preserveAspectRatio="none">
          <g stroke="rgba(0,247,255,.1)">
            <line x1="0" y1="40" x2="220" y2="40"/><line x1="0" y1="80" x2="220" y2="80"/>
          </g>
          <polyline fill="none" stroke="#FF6b9d" stroke-width="2" points="0,60 28,55 55,65 82,48 110,62 138,52 165,68 192,56 220,60"/>
          <polyline fill="none" stroke="#00F7FF" stroke-width="2" points="0,90 28,88 55,92 82,86 110,90 138,87 165,93 192,88 220,90"/>
        </svg>
        <div style="font-size:11px;color:var(--gray);margin-top:6px;">
          <span style="color:#FF6b9d;">━ 心率</span>　<span style="color:#00F7FF;">━ 血氧</span>
        </div>
      </div>
    </div>

    <div class="pcard" style="margin-top:20px;">
      <h4>AI 生理评估结论与建议</h4>
      <div class="advice">
        <ul>
          <li>心率、血氧、HRV 等核心指标<b>均在正常范围</b>，心肺功能状态良好。</li>
          <li>呼吸率 25.5 次/分<b style="color:var(--yellow);">高于静息参考区间</b>，可能与当前专注/紧张状态有关，建议做深呼吸调节。</li>
          <li>建议保持每周 150 分钟中等强度有氧运动，维持良好心血管状态。</li>
        </ul>
      </div>
    </div>
  `
},

/* ================= 肌肤健康评估 ================= */
skin: {
  icon:'skin', title:'肌肤健康评估', sub:'SKIN HEALTH ASSESSMENT',
  render: () => `
    <div class="grid-2" style="margin-bottom:20px;">
      <div class="pcard hi">
        <h4>肤质综合评分</h4>
        <div class="ring-box">
          <div class="ring">
            <svg viewBox="0 0 120 120">
              <circle cx="60" cy="60" r="50" fill="none" stroke="rgba(255,255,255,.06)" stroke-width="9"/>
              <circle cx="60" cy="60" r="50" fill="none" stroke="#00F7FF" stroke-width="9"
                stroke-linecap="round" stroke-dasharray="314" stroke-dashoffset="82"
                style="filter:drop-shadow(0 0 6px #00F7FF)"/>
            </svg>
            <div class="rv"><b>74</b><span>肤质评分</span></div>
          </div>
          <div style="flex:1;">
            <div style="margin-bottom:10px;"><span class="tinytag c">混合性肤质</span> <span class="tinytag o">T区偏油</span> <span class="tinytag g">无痘</span></div>
            <div style="font-size:12px;color:var(--gray);line-height:1.9;">
              基于高清人脸图像的 AI 肤质分析，整体肤况<b style="color:var(--cyan);">中等偏好</b>，
              建议加强保湿与防晒。
            </div>
          </div>
        </div>
      </div>

      <div class="pcard">
        <h4>面部分区热力图</h4>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:center;">
          <div>
            <div class="face-map" style="max-width:180px;margin:0 auto;"></div>
            <div class="face-legend" style="margin-top:10px;max-width:180px;margin-left:auto;margin-right:auto;">
              <div class="lb"><span class="sw" style="background:rgba(0,225,166,.7);"></span>健康</div>
              <div class="lb"><span class="sw" style="background:rgba(255,212,102,.7);"></span>关注</div>
              <div class="lb"><span class="sw" style="background:rgba(255,92,92,.7);"></span>异常</div>
            </div>
          </div>
          <div>
            <div class="kv"><span class="k">T区（额头/鼻）</span><span class="v warn">油脂偏多</span></div>
            <div class="kv"><span class="k">U区（脸颊）</span><span class="v ok">水油平衡</span></div>
            <div class="kv"><span class="k">眼周</span><span class="v warn">轻度黑眼圈</span></div>
            <div class="kv"><span class="k">下巴</span><span class="v ok">正常</span></div>
          </div>
        </div>
      </div>
    </div>

    <div class="pcard" style="margin-bottom:20px;">
      <h4>八维肤质指标</h4>
      <div class="grid-2" style="gap:8px 32px;">
        <div class="hbar"><div class="t"><span>水分度</span><b>62</b></div><div class="b"><i style="width:62%;background:linear-gradient(90deg,#00F7FF,#9BFFFB);"></i></div></div>
        <div class="hbar"><div class="t"><span>油脂度</span><b>71</b></div><div class="b"><i style="width:71%;background:linear-gradient(90deg,#FFD466,#FFE58A);"></i></div></div>
        <div class="hbar"><div class="t"><span>光泽度</span><b>68</b></div><div class="b"><i style="width:68%;background:linear-gradient(90deg,#a78bfa,#c4b5fd);"></i></div></div>
        <div class="hbar"><div class="t"><span>毛孔细腻度</span><b>58</b></div><div class="b"><i style="width:58%;background:linear-gradient(90deg,#FFD466,#FFE58A);"></i></div></div>
        <div class="hbar"><div class="t"><span>肤色均匀度</span><b>76</b></div><div class="b"><i style="width:76%;background:linear-gradient(90deg,#00E1A6,#00F7FF);"></i></div></div>
        <div class="hbar"><div class="t"><span>皱纹 / 细纹</span><b>82</b></div><div class="b"><i style="width:82%;background:linear-gradient(90deg,#00E1A6,#00F7FF);"></i></div></div>
        <div class="hbar"><div class="t"><span>色斑指数</span><b>79</b></div><div class="b"><i style="width:79%;background:linear-gradient(90deg,#00E1A6,#00F7FF);"></i></div></div>
        <div class="hbar"><div class="t"><span>敏感度</span><b>85</b></div><div class="b"><i style="width:85%;background:linear-gradient(90deg,#00E1A6,#00F7FF);"></i></div></div>
      </div>
    </div>

    <div class="pcard">
      <h4>AI 护肤建议</h4>
      <div class="advice">
        <ul>
          <li><b>控油补水</b>：T区油脂偏多而整体水分中等，建议选用清爽型保湿产品，避免过度清洁。</li>
          <li><b>眼周护理</b>：检测到轻度黑眼圈，与疲劳指数相关，建议保证 7 小时以上睡眠并热敷眼周。</li>
          <li><b>防晒</b>：肤色均匀度良好，坚持日常 SPF30+ 防晒可预防色斑加深。</li>
        </ul>
      </div>
    </div>
  `
},

/* ================= 特殊场景评估 ================= */
scene: {
  icon:'scene', title:'特殊场景评估', sub:'SPECIAL SCENARIO ASSESSMENT',
  render: () => `
    <div class="pcard" style="margin-bottom:20px;">
      <h4>选择评估场景</h4>
      <div style="font-size:12px;color:var(--gray);margin-bottom:16px;line-height:1.8;">
        针对不同应用场景提供专项状态评估模型，点击卡片可启动对应场景的实时评估。
      </div>
      <div class="grid-3">
        <div class="scene-card">
          <div class="si">${ICONS.car}</div>
          <h5>疲劳驾驶监测</h5>
          <p>PERCLOS · 眨眼频率 · 哈欠检测 · 头姿点头，实时预警驾驶疲劳风险</p>
          <div class="st"><span class="tinytag g">可用</span></div>
        </div>
        <div class="scene-card">
          <div class="si">${ICONS.work}</div>
          <h5>办公专注度评估</h5>
          <p>注意力评分 · 视线离屏率 · 久坐提醒，量化办公效率与用眼健康</p>
          <div class="st"><span class="tinytag g">可用</span></div>
        </div>
        <div class="scene-card">
          <div class="si">${ICONS.study}</div>
          <h5>在线学习状态</h5>
          <p>专注度 · 困倦度 · 情绪投入度分析，辅助自适应学习节奏调整</p>
          <div class="st"><span class="tinytag g">可用</span></div>
        </div>
        <div class="scene-card">
          <div class="si">${ICONS.sport}</div>
          <h5>运动恢复评估</h5>
          <p>运动后心率恢复速率 · HRV 变化，评估体能恢复与训练负荷</p>
          <div class="st"><span class="tinytag c">Beta</span></div>
        </div>
        <div class="scene-card">
          <div class="si">${ICONS.mic}</div>
          <h5>面试 / 演讲压力</h5>
          <p>紧张度 · 微表情 · 语音节律联合分析，提供临场心理状态反馈</p>
          <div class="st"><span class="tinytag c">Beta</span></div>
        </div>
        <div class="scene-card">
          <div class="si">${ICONS.moon}</div>
          <h5>睡前放松评估</h5>
          <p>唤醒度 · 呼吸节律 · HRV 放松指数，辅助入睡准备度判断</p>
          <div class="st"><span class="tinytag o">开发中</span></div>
        </div>
      </div>
    </div>

    <div class="grid-2">
      <div class="pcard">
        <h4>当前场景示例 <span class="tag">办公专注度</span></h4>
        <div class="grid-3" style="margin-bottom:14px;">
          <div style="text-align:center;padding:12px;background:rgba(0,225,166,.06);border-radius:4px;">
            <div class="big-num" style="font-size:30px;color:var(--green);">76</div>
            <div style="font-size:10px;color:var(--gray);letter-spacing:1px;margin-top:4px;">专注度</div>
          </div>
          <div style="text-align:center;padding:12px;background:rgba(0,247,255,.06);border-radius:4px;">
            <div class="big-num" style="font-size:30px;color:var(--cyan);">4%</div>
            <div style="font-size:10px;color:var(--gray);letter-spacing:1px;margin-top:4px;">视线离屏率</div>
          </div>
          <div style="text-align:center;padding:12px;background:rgba(255,212,102,.06);border-radius:4px;">
            <div class="big-num" style="font-size:30px;color:var(--yellow);">86'</div>
            <div style="font-size:10px;color:var(--gray);letter-spacing:1px;margin-top:4px;">久坐时长</div>
          </div>
        </div>
        <div class="kv"><span class="k">持续专注时长</span><span class="v">23 分钟</span></div>
        <div class="kv"><span class="k">疲劳预警</span><span class="v ok">无</span></div>
        <div class="kv"><span class="k">建议休息</span><span class="v warn">86 分钟 · 建议起身活动</span></div>
      </div>

      <div class="pcard">
        <h4>场景评估记录</h4>
        <div class="timeline">
          <div class="item"><span class="time">14:32</span><span class="t">办公专注度评估</span><div class="d">得分 76 · 正常水平 · 持续 30 分钟</div></div>
          <div class="item"><span class="time">11:05</span><span class="t">疲劳驾驶监测</span><div class="d">30 分钟车程 · 无预警事件</div></div>
          <div class="item"><span class="time">昨天</span><span class="t">在线学习状态</span><div class="d">专注度 82 · 优秀 · 学习 45 分钟</div></div>
          <div class="item"><span class="time">08-09</span><span class="t">面试压力评估</span><div class="d">紧张度中等 · 心率上升 12 bpm</div></div>
        </div>
      </div>
    </div>
  `
},

/* ================= 个人终端健康方案 ================= */
plan: {
  icon:'plan', title:'个人终端健康方案', sub:'PERSONAL HEALTH SOLUTION',
  render: () => `
    <div class="grid-2" style="margin-bottom:20px;">
      <div class="pcard hi">
        <h4>个人健康画像</h4>
        <div style="display:flex;gap:16px;align-items:center;margin-bottom:16px;">
          <div style="width:64px;height:64px;border-radius:50%;background:linear-gradient(135deg,#00F7FF,#0066ff);display:flex;align-items:center;justify-content:center;color:#050d1c;font-size:22px;font-weight:bold;flex:0 0 64px;box-shadow:0 0 16px rgba(0,247,255,.4);">HK</div>
          <div style="flex:1;">
            <div style="font-size:15px;color:var(--white);margin-bottom:6px;letter-spacing:1px;">用户 · HiKO-001</div>
            <div><span class="tinytag g">心理良好</span> <span class="tinytag g">心肺正常</span> <span class="tinytag o">呼吸偏高</span> <span class="tinytag o">中度疲劳</span> <span class="tinytag c">混合性肤质</span></div>
          </div>
        </div>
        <div class="kv"><span class="k">累计监测天数</span><span class="v">36 天</span></div>
        <div class="kv"><span class="k">本周健康趋势</span><span class="v ok">↗ 上升 3.2%</span></div>
        <div class="kv"><span class="k">方案完成率</span><span class="v">78 %</span></div>
      </div>

      <div class="pcard">
        <h4>健康目标进度</h4>
        <div class="hbar"><div class="t"><span>降低呼吸率至 20 以下</span><b>60%</b></div><div class="b"><i style="width:60%;"></i></div></div>
        <div class="hbar"><div class="t"><span>疲劳指数降至 30 以下</span><b>45%</b></div><div class="b"><i style="width:45%;background:linear-gradient(90deg,#FFD466,#FFE58A);"></i></div></div>
        <div class="hbar"><div class="t"><span>每日放松训练 10 分钟</span><b>78%</b></div><div class="b"><i style="width:78%;background:linear-gradient(90deg,#00E1A6,#00F7FF);"></i></div></div>
        <div class="hbar"><div class="t"><span>用眼休息（20-20-20）</span><b>52%</b></div><div class="b"><i style="width:52%;background:linear-gradient(90deg,#a78bfa,#c4b5fd);"></i></div></div>
      </div>
    </div>

    <div class="pcard" style="margin-bottom:20px;">
      <h4>今日个性化方案</h4>
      <div class="timeline">
        <div class="item"><span class="time">09:00</span><span class="t">晨间状态自检（2 分钟面部扫描）</span> <span class="tinytag g" style="margin-left:8px;">已完成</span></div>
        <div class="item"><span class="time">11:00</span><span class="t">4-7-8 呼吸放松训练 5 分钟</span> <span class="tinytag g" style="margin-left:8px;">已完成</span><div class="d">目标呼吸率 &lt; 20 次/分</div></div>
        <div class="item"><span class="time">14:30</span><span class="t">久坐提醒：起身活动 5 分钟 + 远眺放松</span> <span class="tinytag o" style="margin-left:8px;">进行中</span></div>
        <div class="item"><span class="time">18:00</span><span class="t">中等强度有氧运动 30 分钟（快走 / 慢跑）</span></div>
        <div class="item"><span class="time">22:30</span><span class="t">睡前放松评估 + 冥想引导 10 分钟</span></div>
      </div>
    </div>

    <div class="grid-2">
      <div class="pcard">
        <h4>本周健康评分趋势</h4>
        <svg width="100%" height="150" viewBox="0 0 500 150" preserveAspectRatio="none">
          <g stroke="rgba(0,247,255,.1)">
            <line x1="0" y1="35" x2="500" y2="35"/><line x1="0" y1="75" x2="500" y2="75"/><line x1="0" y1="115" x2="500" y2="115"/>
          </g>
          <polyline fill="none" stroke="#00F7FF" stroke-width="2.5" points="20,90 90,82 160,92 230,70 300,64 370,52 440,44" style="filter:drop-shadow(0 0 4px #00F7FF)"/>
          <g fill="#00F7FF">
            <circle cx="20" cy="90" r="3"/><circle cx="90" cy="82" r="3"/><circle cx="160" cy="92" r="3"/>
            <circle cx="230" cy="70" r="3"/><circle cx="300" cy="64" r="3"/><circle cx="370" cy="52" r="3"/>
            <circle cx="440" cy="44" r="4" fill="#fff"/>
          </g>
          <g font-size="10" fill="#9BFFFB" text-anchor="middle">
            <text x="20" y="140">一</text><text x="90" y="140">二</text><text x="160" y="140">三</text>
            <text x="230" y="140">四</text><text x="300" y="140">五</text><text x="370" y="140">六</text><text x="440" y="140">日</text>
          </g>
        </svg>
      </div>
      <div class="pcard">
        <h4>AI 健康管家建议</h4>
        <div class="advice">
          <ul>
            <li>本周健康评分持续上升，<b>放松训练依从性良好</b>，请继续保持。</li>
            <li>下午时段疲劳指数普遍偏高，建议将重要工作安排在上午。</li>
            <li>方案将于每周日晚根据本周数据<b>自动迭代更新</b>。</li>
          </ul>
        </div>
      </div>
    </div>
  `
},

};
