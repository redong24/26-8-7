/* ============ 弹窗面板内容定义 ============ */
const PANELS = {
  psy: {
    icon: "🧠", title: "心理综合评估",
    html: `
    <div class="grid2">
      <div class="pcard">
        <h4>综合心理健康评分</h4>
        <div class="score-ring">
          <div class="ring">
            <svg width="110" height="110">
              <circle cx="55" cy="55" r="46" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="9"/>
              <circle cx="55" cy="55" r="46" fill="none" stroke="#00d4ff" stroke-width="9"
                stroke-linecap="round" stroke-dasharray="289" stroke-dashoffset="55"/>
            </svg>
            <div class="rv"><b>81</b><span>综合评分</span></div>
          </div>
          <div>
            <div style="margin-bottom:8px"><span class="tag g">状态良好</span><span class="tag c">情绪稳定</span></div>
            <div style="font-size:12px;color:var(--txt-dim);line-height:1.9">
              基于近 5 分钟表情、AU、视线与生理指标的多模态融合分析，
              当前心理状态整体<b style="color:#00e58a">良好</b>，无明显焦虑或抑郁倾向。
            </div>
          </div>
        </div>
      </div>
      <div class="pcard">
        <h4>五维心理画像</h4>
        <div class="radar-wrap">
          <svg width="230" height="185" viewBox="0 0 230 185">
            <g stroke="rgba(0,180,255,.25)" fill="none">
              <polygon points="115,18 205,80 172,168 58,168 25,80"/>
              <polygon points="115,48 175,88 153,148 77,148 55,88" opacity=".7"/>
              <polygon points="115,78 145,96 134,128 96,128 85,96" opacity=".5"/>
            </g>
            <polygon points="115,30 188,84 160,152 72,158 42,86" fill="rgba(0,212,255,.25)" stroke="#00d4ff" stroke-width="2"/>
            <g font-size="11" fill="#7fa7cc">
              <text x="115" y="12" text-anchor="middle">情绪稳定 86</text>
              <text x="212" y="78">压力 74</text>
              <text x="180" y="182">专注 79</text>
              <text x="18" y="182">放松 88</text>
              <text x="0" y="78">活力 72</text>
            </g>
          </svg>
        </div>
      </div>
    </div>
    <div class="grid2">
      <div class="pcard">
        <h4>压力 / 焦虑 / 抑郁筛查</h4>
        <div class="hbar"><div class="t"><span>压力水平</span><b>26 / 100 · 低</b></div>
          <div class="b"><i style="width:26%;background:linear-gradient(90deg,#00e58a,#4ade80)"></i></div></div>
        <div class="hbar"><div class="t"><span>焦虑倾向</span><b>18 / 100 · 低</b></div>
          <div class="b"><i style="width:18%;background:linear-gradient(90deg,#00e58a,#4ade80)"></i></div></div>
        <div class="hbar"><div class="t"><span>抑郁倾向</span><b>12 / 100 · 低</b></div>
          <div class="b"><i style="width:12%;background:linear-gradient(90deg,#00e58a,#4ade80)"></i></div></div>
        <div class="hbar"><div class="t"><span>疲劳指数</span><b>41 / 100 · 中</b></div>
          <div class="b"><i style="width:41%;background:linear-gradient(90deg,#ffb340,#ffd166)"></i></div></div>
        <div style="font-size:11px;color:var(--txt-dim);margin-top:8px">* 依据 rPPG-HRV + 微表情 AU 时序特征估计，仅供参考</div>
      </div>
      <div class="pcard">
        <h4>情绪时间线（近 5 分钟）</h4>
        <svg width="100%" height="130" viewBox="0 0 380 130" preserveAspectRatio="none">
          <g stroke="rgba(0,180,255,.15)"><line x1="0" y1="32" x2="380" y2="32"/><line x1="0" y1="65" x2="380" y2="65"/><line x1="0" y1="98" x2="380" y2="98"/></g>
          <polyline fill="none" stroke="#00d4ff" stroke-width="2" points="0,60 40,55 80,62 120,48 160,58 200,52 240,64 280,50 320,58 380,54"/>
          <polyline fill="none" stroke="#ff6b9d" stroke-width="1.5" opacity=".8" points="0,95 40,92 80,96 120,90 160,94 200,88 240,95 280,91 320,96 380,93"/>
          <polyline fill="none" stroke="#ffb340" stroke-width="1.5" opacity=".8" points="0,80 40,84 80,78 120,86 160,80 200,84 240,76 280,82 320,78 380,82"/>
        </svg>
        <div style="font-size:11px;color:var(--txt-dim)">
          <span style="color:#00d4ff">━ 平静</span>　<span style="color:#ffb340">━ 唤醒度</span>　<span style="color:#ff6b9d">━ 负向情绪</span>
        </div>
      </div>
    </div>
    <div class="pcard">
      <h4>AI 心理评估结论与建议</h4>
      <div class="advice">
        <li>当前心理状态<b>整体良好</b>，情绪以平静为主导（41%），无持续负向情绪聚集。</li>
        <li>疲劳指数处于中等区间（41），建议每工作 45 分钟起身活动 3~5 分钟。</li>
        <li>可尝试 <b>4-7-8 呼吸放松法</b> 进一步降低唤醒度，改善呼吸率偏高（25.5 次/分）现象。</li>
      </div>
    </div>`
  },

  phy: {
    icon: "❤️", title: "生理健康评估",
    html: `
    <div class="grid2">
      <div class="pcard">
        <h4>生理健康综合评分</h4>
        <div class="score-ring">
          <div class="ring">
            <svg width="110" height="110">
              <circle cx="55" cy="55" r="46" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="9"/>
              <circle cx="55" cy="55" r="46" fill="none" stroke="#00e58a" stroke-width="9"
                stroke-linecap="round" stroke-dasharray="289" stroke-dashoffset="38"/>
            </svg>
            <div class="rv"><b>87</b><span>健康评分</span></div>
          </div>
          <div>
            <div style="margin-bottom:8px"><span class="tag g">心肺功能正常</span><span class="tag o">呼吸率偏高</span></div>
            <div style="font-size:12px;color:var(--txt-dim);line-height:1.9">
              基于 rPPG 非接触式测量的多项生理指标综合评估，
              心血管指标处于<b style="color:#00e58a">正常范围</b>。
            </div>
          </div>
        </div>
      </div>
      <div class="pcard">
        <h4>核心生理指标</h4>
        <div class="kv"><span class="k">心率 HR</span><span class="v ok">78 bpm（60~100 正常）</span></div>
        <div class="kv"><span class="k">血氧 SpO₂</span><span class="v ok">97 %（≥95 正常）</span></div>
        <div class="kv"><span class="k">呼吸率 RR</span><span class="v warn">25.5 次/分（12~20 偏高）</span></div>
        <div class="kv"><span class="k">血压估计 BP</span><span class="v">118 / 76 mmHg</span></div>
        <div class="kv"><span class="k">HRV · SDNN</span><span class="v ok">52 ms（>50 良好）</span></div>
        <div class="kv"><span class="k">HRV · RMSSD</span><span class="v ok">38 ms</span></div>
      </div>
    </div>
    <div class="grid3">
      <div class="pcard">
        <h4>心血管风险</h4>
        <div class="hbar"><div class="t"><span>心律不齐风险</span><b>低</b></div>
          <div class="b"><i style="width:14%;background:#00e58a"></i></div></div>
        <div class="hbar"><div class="t"><span>血管弹性指数</span><b>良好</b></div>
          <div class="b"><i style="width:78%;background:#00d4ff"></i></div></div>
        <div class="hbar"><div class="t"><span>心脏负荷</span><b>正常</b></div>
          <div class="b"><i style="width:32%;background:#00e58a"></i></div></div>
      </div>
      <div class="pcard">
        <h4>自主神经平衡</h4>
        <svg width="100%" height="100" viewBox="0 0 200 100">
          <path d="M 20 85 A 80 80 0 0 1 180 85" fill="none" stroke="rgba(255,255,255,.1)" stroke-width="12"/>
          <path d="M 20 85 A 80 80 0 0 1 128 22" fill="none" stroke="#00d4ff" stroke-width="12" stroke-linecap="round"/>
          <text x="100" y="72" text-anchor="middle" fill="#fff" font-size="20" font-weight="bold">62%</text>
          <text x="100" y="92" text-anchor="middle" fill="#7fa7cc" font-size="10">交感/副交感均衡度</text>
        </svg>
        <div style="font-size:11px;color:var(--txt-dim);text-align:center">LF/HF = 1.8 · 轻度交感偏向</div>
      </div>
      <div class="pcard">
        <h4>体征趋势（24h）</h4>
        <svg width="100%" height="100" viewBox="0 0 200 100" preserveAspectRatio="none">
          <polyline fill="none" stroke="#ff6b9d" stroke-width="2" points="0,50 25,45 50,55 75,40 100,52 125,44 150,58 175,46 200,50"/>
          <polyline fill="none" stroke="#00d4ff" stroke-width="2" points="0,75 25,72 50,76 75,70 100,74 125,71 150,77 175,72 200,74"/>
        </svg>
        <div style="font-size:11px;color:var(--txt-dim)"><span style="color:#ff6b9d">━ 心率</span>　<span style="color:#00d4ff">━ 血氧</span></div>
      </div>
    </div>
    <div class="pcard">
      <h4>AI 生理评估结论与建议</h4>
      <div class="advice">
        <li>心率、血氧、HRV 等核心指标<b>均在正常范围</b>，心肺功能状态良好。</li>
        <li>呼吸率 25.5 次/分<b style="color:#ffb340">高于静息参考区间</b>，可能与当前专注/紧张状态有关，建议做深呼吸调节。</li>
        <li>建议保持每周 150 分钟中等强度有氧运动，维持良好心血管状态。</li>
      </div>
    </div>`
  },

  skin: {
    icon: "✨", title: "肌肤健康评估",
    html: `
    <div class="grid2">
      <div class="pcard">
        <h4>肤质综合评分</h4>
        <div class="score-ring">
          <div class="ring">
            <svg width="110" height="110">
              <circle cx="55" cy="55" r="46" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="9"/>
              <circle cx="55" cy="55" r="46" fill="none" stroke="#a78bfa" stroke-width="9"
                stroke-linecap="round" stroke-dasharray="289" stroke-dashoffset="75"/>
            </svg>
            <div class="rv"><b>74</b><span>肤质评分</span></div>
          </div>
          <div>
            <div style="margin-bottom:8px"><span class="tag c">混合性肤质</span><span class="tag o">T区偏油</span><span class="tag g">无明显痘痘</span></div>
            <div style="font-size:12px;color:var(--txt-dim);line-height:1.9">
              基于高清人脸图像的 AI 肤质分析，整体肤况<b style="color:#00d4ff">中等偏好</b>，
              建议加强保湿与防晒。
            </div>
          </div>
        </div>
      </div>
      <div class="pcard">
        <h4>面部分区分析</h4>
        <div class="face-map">
          <svg class="face-svg" width="150" height="170" viewBox="0 0 150 170">
            <ellipse cx="75" cy="88" rx="52" ry="66" fill="rgba(0,80,180,.15)" stroke="rgba(0,180,255,.5)"/>
            <rect x="55" y="30" width="40" height="26" rx="6" fill="rgba(255,179,64,.3)" stroke="#ffb340"/>
            <text x="75" y="47" text-anchor="middle" font-size="9" fill="#ffd">额头·偏油</text>
            <rect x="62" y="62" width="26" height="40" rx="6" fill="rgba(255,179,64,.3)" stroke="#ffb340"/>
            <text x="75" y="85" text-anchor="middle" font-size="9" fill="#ffd">鼻部</text>
            <circle cx="38" cy="95" r="15" fill="rgba(0,229,138,.25)" stroke="#00e58a"/>
            <text x="38" y="99" text-anchor="middle" font-size="9" fill="#cfd">脸颊</text>
            <circle cx="112" cy="95" r="15" fill="rgba(0,229,138,.25)" stroke="#00e58a"/>
            <text x="112" y="99" text-anchor="middle" font-size="9" fill="#cfd">脸颊</text>
            <rect x="58" y="122" width="34" height="20" rx="6" fill="rgba(0,212,255,.25)" stroke="#00d4ff"/>
            <text x="75" y="135" text-anchor="middle" font-size="9" fill="#cfd">下巴</text>
          </svg>
          <div style="flex:1;font-size:12px">
            <div class="kv"><span class="k">T区（额头/鼻）</span><span class="v warn">油脂分泌偏多</span></div>
            <div class="kv"><span class="k">U区（脸颊）</span><span class="v ok">水油平衡</span></div>
            <div class="kv"><span class="k">眼周</span><span class="v warn">轻度黑眼圈</span></div>
            <div class="kv"><span class="k">下巴</span><span class="v ok">正常</span></div>
          </div>
        </div>
      </div>
    </div>
    <div class="pcard">
      <h4>八维肤质指标</h4>
      <div class="grid2" style="gap:4px 30px">
        <div class="hbar"><div class="t"><span>水分度</span><b>62</b></div><div class="b"><i style="width:62%;background:#00d4ff"></i></div></div>
        <div class="hbar"><div class="t"><span>油脂度</span><b>71</b></div><div class="b"><i style="width:71%;background:#ffb340"></i></div></div>
        <div class="hbar"><div class="t"><span>光泽度</span><b>68</b></div><div class="b"><i style="width:68%;background:#a78bfa"></i></div></div>
        <div class="hbar"><div class="t"><span>毛孔细腻度</span><b>58</b></div><div class="b"><i style="width:58%;background:#ffb340"></i></div></div>
        <div class="hbar"><div class="t"><span>肤色均匀度</span><b>76</b></div><div class="b"><i style="width:76%;background:#00e58a"></i></div></div>
        <div class="hbar"><div class="t"><span>皱纹/细纹</span><b>82</b></div><div class="b"><i style="width:82%;background:#00e58a"></i></div></div>
        <div class="hbar"><div class="t"><span>色斑指数</span><b>79</b></div><div class="b"><i style="width:79%;background:#00e58a"></i></div></div>
        <div class="hbar"><div class="t"><span>敏感度</span><b>85</b></div><div class="b"><i style="width:85%;background:#00e58a"></i></div></div>
      </div>
    </div>
    <div class="pcard">
      <h4>AI 护肤建议</h4>
      <div class="advice">
        <li><b>控油补水</b>：T区油脂偏多而整体水分中等，建议选用清爽型保湿产品，避免过度清洁。</li>
        <li><b>眼周护理</b>：检测到轻度黑眼圈，与疲劳指数相关，建议保证 7 小时以上睡眠并热敷眼周。</li>
        <li><b>防晒</b>：肤色均匀度良好，坚持日常 SPF30+ 防晒可预防色斑加深。</li>
      </div>
    </div>`
  },

  scene: {
    icon: "🎯", title: "特殊场景评估",
    html: `
    <div class="pcard" style="margin-bottom:16px">
      <h4>选择评估场景</h4>
      <div style="font-size:12px;color:var(--txt-dim);margin-bottom:14px">
        针对不同应用场景提供专项状态评估模型，点击卡片可启动对应场景的实时评估。
      </div>
      <div class="grid3">
        <div class="scene-card">
          <span class="si">🚗</span><h5>疲劳驾驶监测</h5>
          <p>PERCLOS·眨眼频率·哈欠检测·头姿点头，实时预警驾驶疲劳风险</p>
          <div class="st"><span class="tag g">可用</span></div>
        </div>
        <div class="scene-card">
          <span class="si">💼</span><h5>办公专注度评估</h5>
          <p>注意力评分·视线离屏率·久坐提醒，量化办公效率与用眼健康</p>
          <div class="st"><span class="tag g">可用</span></div>
        </div>
        <div class="scene-card">
          <span class="si">📚</span><h5>在线学习状态</h5>
          <p>专注度·困倦度·情绪投入度分析，辅助自适应学习节奏调整</p>
          <div class="st"><span class="tag g">可用</span></div>
        </div>
        <div class="scene-card">
          <span class="si">🏃</span><h5>运动恢复评估</h5>
          <p>运动后心率恢复速率·HRV 变化，评估体能恢复与训练负荷</p>
          <div class="st"><span class="tag c">Beta</span></div>
        </div>
        <div class="scene-card">
          <span class="si">🎤</span><h5>面试/演讲压力</h5>
          <p>紧张度·微表情·语音节律联合分析，提供临场心理状态反馈</p>
          <div class="st"><span class="tag c">Beta</span></div>
        </div>
        <div class="scene-card">
          <span class="si">🛌</span><h5>睡前放松评估</h5>
          <p>唤醒度·呼吸节律·HRV 放松指数，辅助入睡准备度判断</p>
          <div class="st"><span class="tag o">开发中</span></div>
        </div>
      </div>
    </div>
    <div class="grid2">
      <div class="pcard">
        <h4>当前场景示例 · 办公专注度</h4>
        <div class="kv"><span class="k">专注度评分</span><span class="v ok">76 / 100</span></div>
        <div class="kv"><span class="k">视线离屏率（10min）</span><span class="v ok">4 %</span></div>
        <div class="kv"><span class="k">持续专注时长</span><span class="v">23 分钟</span></div>
        <div class="kv"><span class="k">久坐时长</span><span class="v warn">86 分钟 · 建议起身</span></div>
        <div class="kv"><span class="k">疲劳预警</span><span class="v ok">无</span></div>
      </div>
      <div class="pcard">
        <h4>场景评估记录</h4>
        <div class="plan-tl">
          <div class="item"><span class="time">14:32</span><span class="desc">办公专注度评估 · 得分 76 · 正常</span></div>
          <div class="item"><span class="time">11:05</span><span class="desc">疲劳驾驶监测 · 30 分钟 · 无预警</span></div>
          <div class="item"><span class="time">昨天</span><span class="desc">在线学习状态 · 专注度 82 · 优秀</span></div>
          <div class="item"><span class="time">08-09</span><span class="desc">面试压力评估 · 紧张度中等</span></div>
        </div>
      </div>
    </div>`
  },

  plan: {
    icon: "📋", title: "个人终端健康方案",
    html: `
    <div class="grid2">
      <div class="pcard">
        <h4>个人健康画像</h4>
        <div style="display:flex;gap:16px;align-items:center">
          <div style="width:64px;height:64px;border-radius:50%;background:linear-gradient(135deg,#0af,#07f);
            display:flex;align-items:center;justify-content:center;font-size:28px;flex:0 0 64px">👤</div>
          <div style="flex:1">
            <div style="font-size:15px;color:#fff;margin-bottom:6px">用户 · HiKO-001</div>
            <span class="tag g">心理状态良好</span><span class="tag g">心肺功能正常</span>
            <span class="tag o">呼吸率偏高</span><span class="tag o">中度疲劳</span><span class="tag c">混合性肤质</span>
          </div>
        </div>
        <div style="margin-top:12px">
          <div class="kv"><span class="k">累计监测天数</span><span class="v">36 天</span></div>
          <div class="kv"><span class="k">本周健康趋势</span><span class="v ok">↗ 上升 3.2%</span></div>
          <div class="kv"><span class="k">方案完成率</span><span class="v">78 %</span></div>
        </div>
      </div>
      <div class="pcard">
        <h4>健康目标进度</h4>
        <div class="hbar"><div class="t"><span>💧 降低呼吸率至 20 以下</span><b>60%</b></div>
          <div class="b"><i style="width:60%;background:#00d4ff"></i></div></div>
        <div class="hbar"><div class="t"><span>😴 疲劳指数降至 30 以下</span><b>45%</b></div>
          <div class="b"><i style="width:45%;background:#ffb340"></i></div></div>
        <div class="hbar"><div class="t"><span>🧘 每日放松训练 10 分钟</span><b>78%</b></div>
          <div class="b"><i style="width:78%;background:#00e58a"></i></div></div>
        <div class="hbar"><div class="t"><span>👁 用眼休息(20-20-20法则)</span><b>52%</b></div>
          <div class="b"><i style="width:52%;background:#a78bfa"></i></div></div>
      </div>
    </div>
    <div class="pcard">
      <h4>今日个性化方案</h4>
      <div class="plan-tl">
        <div class="item"><span class="time">09:00</span><span class="desc">☀️ 晨间状态自检（2 分钟面部扫描）— <b style="color:#00e58a">已完成</b></span></div>
        <div class="item"><span class="time">11:00</span><span class="desc">🧘 4-7-8 呼吸放松训练 5 分钟，目标呼吸率 &lt; 20 次/分 — <b style="color:#00e58a">已完成</b></span></div>
        <div class="item"><span class="time">14:30</span><span class="desc">🚶 久坐提醒：起身活动 5 分钟 + 远眺放松 — <b style="color:#ffb340">进行中</b></span></div>
        <div class="item"><span class="time">18:00</span><span class="desc">🏃 中等强度有氧运动 30 分钟（快走/慢跑）</span></div>
        <div class="item"><span class="time">22:30</span><span class="desc">🛌 睡前放松评估 + 冥想引导 10 分钟</span></div>
      </div>
    </div>
    <div class="grid2">
      <div class="pcard">
        <h4>本周健康评分趋势</h4>
        <svg width="100%" height="120" viewBox="0 0 380 120" preserveAspectRatio="none">
          <g stroke="rgba(0,180,255,.15)"><line x1="0" y1="30" x2="380" y2="30"/><line x1="0" y1="60" x2="380" y2="60"/><line x1="0" y1="90" x2="380" y2="90"/></g>
          <polyline fill="none" stroke="#00d4ff" stroke-width="2.5" points="10,70 65,64 120,72 175,55 230,50 285,42 340,36"/>
          <g fill="#00d4ff">
            <circle cx="10" cy="70" r="3"/><circle cx="65" cy="64" r="3"/><circle cx="120" cy="72" r="3"/>
            <circle cx="175" cy="55" r="3"/><circle cx="230" cy="50" r="3"/><circle cx="285" cy="42" r="3"/>
            <circle cx="340" cy="36" r="4" fill="#fff"/>
          </g>
          <g font-size="10" fill="#7fa7cc" text-anchor="middle">
            <text x="10" y="112">一</text><text x="65" y="112">二</text><text x="120" y="112">三</text>
            <text x="175" y="112">四</text><text x="230" y="112">五</text><text x="285" y="112">六</text><text x="340" y="112">日</text>
          </g>
        </svg>
      </div>
      <div class="pcard">
        <h4>AI 健康管家建议</h4>
        <div class="advice">
          <li>本周健康评分持续上升，<b>放松训练依从性良好</b>，请继续保持。</li>
          <li>下午时段疲劳指数普遍偏高，建议将重要工作安排在上午。</li>
          <li>方案将于每周日晚根据本周数据<b>自动迭代更新</b>。</li>
        </div>
      </div>
    </div>`
  }
};

/* ============ 交互逻辑 ============ */
(function () {
  const overlay = document.getElementById("overlay");
  const navItems = document.querySelectorAll(".nav-item");
  let popup = null, current = null;

  function buildPopup(key) {
    const cfg = PANELS[key];
    const el = document.createElement("div");
    el.className = "popup";
    el.innerHTML = `
      <div class="p-head">
        <span class="p-ico">${cfg.icon}</span><h2>${cfg.title}</h2>
        <div class="close" title="关闭">✕</div>
      </div>
      <div class="p-body">${cfg.html}</div>`;
    el.querySelector(".close").onclick = closePopup;
    document.body.appendChild(el);
    return el;
  }

  function openPopup(key) {
    if (current === key) { closePopup(); return; }
    if (popup) { popup.remove(); popup = null; }
    current = key;
    popup = buildPopup(key);
    overlay.classList.add("show");
    requestAnimationFrame(() => requestAnimationFrame(() => popup.classList.add("show")));
    setActive(key);
  }

  function closePopup() {
    if (!popup) return;
    popup.classList.remove("show");
    overlay.classList.remove("show");
    const p = popup; popup = null; current = null;
    setTimeout(() => p.remove(), 400);
    setActive("home");
  }

  function setActive(key) {
    navItems.forEach(n => n.classList.toggle("active", n.dataset.panel === key));
  }

  navItems.forEach(n => {
    n.addEventListener("click", () => {
      const key = n.dataset.panel;
      if (key === "home") { closePopup(); return; }
      openPopup(key);
    });
  });
  overlay.addEventListener("click", closePopup);
  document.addEventListener("keydown", e => { if (e.key === "Escape") closePopup(); });
})();
