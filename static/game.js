// static/game.js - 歴史しりとり AI アリーナ フロントエンド制御 (9.3節)

let lastStateVersion = -1;
let pollTimer = null;
let modalClosed = false;

// ページ読み込み完了時
document.addEventListener("DOMContentLoaded", () => {
  fetchState();
  // 9.3節: 2秒間隔でポーリング
  pollTimer = setInterval(fetchState, 2000);
});

async function fetchState() {
  try {
    const res = await fetch("/api/state");
    if (!res.ok) return;
    const data = await res.json();

    // state_version が変化した場合のみ再描画
    if (data.state_version !== lastStateVersion) {
      renderState(data);
      lastStateVersion = data.state_version;
    }
  } catch (err) {
    console.error("Failed to fetch state:", err);
  }
}

function renderState(state) {
  const status = state.status || "idle";

  // 1. ヘッダーバッジとテキスト
  const statusBadge = document.getElementById("statusBadge");
  const statusText = document.getElementById("statusText");

  const statusMap = {
    idle: { label: "待機中 (IDLE)", class: "status-idle" },
    starting: { label: "開始処理中 (STARTING)", class: "status-starting" },
    player_turn: { label: "回答待ち (PLAYER_TURN)", class: "status-player_turn" },
    judging: { label: "判定・採点中 (JUDGING)", class: "status-judging" },
    finalizing: { label: "終了処理中 (FINALIZING)", class: "status-finalizing" },
    ended: { label: "大会終了 (ENDED)", class: "status-ended" }
  };

  const statusInfo = statusMap[status] || { label: status, class: "status-idle" };
  statusBadge.className = `status-badge ${statusInfo.class}`;
  statusBadge.textContent = statusInfo.label;
  statusText.textContent = statusInfo.label;

  // 2. コントロールボタン制御 (9.1節)
  const btnStart = document.getElementById("btnStart");
  const btnStop = document.getElementById("btnStop");
  const btnReset = document.getElementById("btnReset");

  btnStart.disabled = !(status === "idle" || status === "ended");
  btnStop.disabled = !(status === "starting" || status === "player_turn" || status === "judging");
  btnReset.disabled = !(status === "idle" || status === "ended");

  // 3. ステータスバナー
  document.getElementById("totalTurnsText").textContent = `${state.total_turns || 0} / 50`;
  document.getElementById("prefixText").textContent = state.required_prefix || "-";

  const reasonMap = {
    start: "(開始文字)",
    n_rule: "(「ん」ルール)",
    fallback: "(縮退候補)"
  };
  document.getElementById("prefixReasonText").textContent = reasonMap[state.prefix_reason] || "";

  document.getElementById("currentPlayerText").textContent = state.current_player || "-";

  // 4. スコアボード & プレイヤーカード
  const scores = state.scores || { PlayerA: 0, PlayerB: 0, PlayerC: 0 };
  const disqualified = state.disqualified;
  const currentPlayer = state.current_player;

  ["PlayerA", "PlayerB", "PlayerC"].forEach((p) => {
    const card = document.getElementById(`card${p}`);
    const scoreElem = document.getElementById(`score${p}`);
    const prevScore = parseInt(scoreElem.textContent, 10);
    const newScore = scores[p] || 0;

    // スコア更新 & アニメーション
    if (newScore !== prevScore) {
      scoreElem.textContent = newScore;
      scoreElem.classList.remove("score-updated");
      void scoreElem.offsetWidth; // リフロー発生
      scoreElem.classList.add("score-updated");
    }

    // 手番ハイライト
    if (status === "player_turn" && currentPlayer === p) {
      card.classList.add("active-turn");
    } else {
      card.classList.remove("active-turn");
    }

    // 失格表示
    const badge = document.getElementById(`statusBadge${p}`);
    if (disqualified === p) {
      card.classList.add("disqualified");
      badge.className = "player-status-badge status-disqualified";
      badge.textContent = "失格";
    } else if (status === "player_turn" && currentPlayer === p) {
      badge.className = "player-status-badge status-thinking";
      badge.textContent = "思考中...";
    } else {
      badge.className = "player-status-badge status-waiting";
      badge.textContent = "待機中";
    }
  });

  // 最新回答・コメントの更新
  const turns = state.turns || [];
  if (turns.length > 0) {
    const lastTurn = turns[turns.length - 1];
    const lastPlayer = lastTurn.player;
    const ans = lastTurn.answer;
    if (ans) {
      const ansElem = document.getElementById(`answer${lastPlayer}`);
      if (ansElem) {
        ansElem.textContent = `${ans.full_name} (${ans.identifier})`;
      }
    }
  }

  // 直近の返信コメント & 通信ログのアコーディオン更新
  if (state.last_reply) {
    const fromP = state.last_reply.from;
    const commentElem = document.getElementById(`comment${fromP}`);
    if (commentElem && state.last_reply.comment) {
      commentElem.textContent = `「${state.last_reply.comment}」`;
    }
    const msgPre = document.getElementById(`msg${fromP}`);
    if (msgPre) {
      msgPre.textContent = JSON.stringify(state.last_reply, null, 2);
    }
  }

  // 5. 審判解説パネル & ターン履歴テーブルの表示切替
  const judgePanel = document.getElementById("judgePanel");
  const historySection = document.getElementById("historySection");

  if (status === "idle") {
    // 9.2節: idle の間は非表示
    judgePanel.style.display = "none";
    historySection.style.display = "none";
  } else {
    judgePanel.style.display = "block";
    historySection.style.display = "block";
  }

  // 審判解説パネル内容更新
  if (turns.length > 0) {
    const latestTurn = turns[turns.length - 1];
    document.getElementById("commentaryBox").textContent = latestTurn.commentary || "実況生成中...";

    const expList = document.getElementById("explanationList");
    expList.innerHTML = "";
    (latestTurn.explanation || []).forEach((exp) => {
      const row = document.createElement("div");
      row.className = "explanation-row";

      const header = document.createElement("div");
      header.className = "exp-header";

      const name = document.createElement("span");
      name.className = "exp-item-name";
      name.textContent = exp.item;

      const tag = document.createElement("span");
      const src = exp.source || "llm";
      tag.className = `source-tag tag-${src}`;
      const tagLabels = {
        wikipedia_ja: "[Wikipediaより]",
        wikidata: "[Wikidataより]",
        japansearch: "[JapanSearchより]",
        llm: "[LLM補足]"
      };
      tag.textContent = tagLabels[src] || `[${src}]`;

      header.appendChild(name);
      header.appendChild(tag);

      const text = document.createElement("div");
      text.className = "exp-item-text";
      text.textContent = exp.text;

      row.appendChild(header);
      row.appendChild(text);
      expList.appendChild(row);
    });

    // 採点内訳
    const sc = latestTurn.score || {};
    document.getElementById("scoreBase").textContent = sc.base || 0;
    const bonusVal = (sc.bonus && sc.bonus.strategy ? sc.bonus.strategy.score : 0) +
                     (sc.bonus && sc.bonus.education ? sc.bonus.education.score : 0) +
                     (sc.bonus && sc.bonus.rarity ? sc.bonus.rarity.score : 0);
    document.getElementById("scoreBonus").textContent = `+${bonusVal}`;
    document.getElementById("scorePenalty").textContent = sc.penalty ? `${sc.penalty}` : "0";
    document.getElementById("scoreTotal").textContent = sc.total || 0;
  }

  // ターン履歴テーブルの更新
  const tbody = document.getElementById("historyTableBody");
  tbody.innerHTML = "";
  turns.slice().reverse().forEach((t) => {
    const tr = document.createElement("tr");
    const ans = t.answer || {};
    const vClass = t.verdict === "valid" ? "verdict-valid" : "verdict-invalid";
    const vLabel = t.verdict === "valid" ? "合格" : `無効 (${t.error_code || 'e-1'})`;

    tr.innerHTML = `
      <td>${t.turn}</td>
      <td><strong>${t.player}</strong></td>
      <td>${ans.full_name || "-"}</td>
      <td>${ans.identifier || "-"} (${ans.identifier_reading || "-"})</td>
      <td class="${vClass}">${vLabel}</td>
      <td><strong>${t.score ? t.score.total : 0}</strong> pts</td>
      <td>${(t.elapsed_ms / 1000).toFixed(1)}s</td>
    `;
    tbody.appendChild(tr);
  });

  // 6. ゲームログ (直近20件)
  const logBox = document.getElementById("gameLogBox");
  logBox.innerHTML = "";
  const logs = state.log || [];
  logs.slice(-20).forEach((entry) => {
    const div = document.createElement("div");
    div.className = `log-entry log-${entry.type || 'info'}`;
    const timeStr = entry.time ? entry.time.substring(11, 19) : "";
    div.innerHTML = `<span class="log-time">[${timeStr}]</span>${escapeHtml(entry.message)}`;
    logBox.appendChild(div);
  });
  logBox.scrollTop = logBox.scrollHeight;

  // 7. 勝者モーダル (9.2節)
  if (status === "ended" && !modalClosed) {
    showWinnerModal(state);
  } else if (status !== "ended") {
    modalClosed = false;
    hideWinnerModal();
  }
}

function showWinnerModal(state) {
  const modal = document.getElementById("winnerModal");
  const winnersElem = document.getElementById("modalWinners");
  const summaryElem = document.getElementById("modalSummary");

  const winners = state.winners || [];
  if (winners.length === 0) {
    winnersElem.textContent = "勝者なし（不成立）";
    winnersElem.style.color = "#94a3b8";
  } else if (winners.length > 1) {
    winnersElem.textContent = `並列優勝: ${winners.join(" & ")}`;
    winnersElem.style.color = "#f59e0b";
  } else {
    winnersElem.textContent = `優勝: ${winners[0]}`;
    winnersElem.style.color = "#f59e0b";
  }

  let summaryText = state.final_summary || "対戦が終了しました。";
  if (["START_CHAR_EXHAUSTED", "WATCHDOG", "INTERNAL_ERROR"].includes(state.end_reason)) {
    summaryText = `【参考結果: 強制終了（${state.end_reason}）】\n\n` + summaryText;
  }

  summaryElem.textContent = summaryText;
  modal.classList.add("show");
}

function closeModal() {
  modalClosed = true;
  hideWinnerModal();
}

function hideWinnerModal() {
  const modal = document.getElementById("winnerModal");
  modal.classList.remove("show");
}

// REST API 呼び出し
async function startGame() {
  try {
    const res = await fetch("/api/start", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(`開始できません: ${err.error || res.statusText}`);
    }
    fetchState();
  } catch (err) {
    alert(`通信エラー: ${err}`);
  }
}

async function stopGame() {
  try {
    const res = await fetch("/api/stop", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(`停止できません: ${err.error || res.statusText}`);
    }
    fetchState();
  } catch (err) {
    alert(`通信エラー: ${err}`);
  }
}

async function resetGame() {
  try {
    const res = await fetch("/api/reset", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(`リセットできません: ${err.error || res.statusText}`);
    }
    fetchState();
  } catch (err) {
    alert(`通信エラー: ${err}`);
  }
}

function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/[&<>"']/g, (m) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[m]);
}
