// ==========================================
// ページ切り替え（グローバル関数 - HTML の onclick から呼ばれる）
// ==========================================
function switchPage(pageName) {
    const pages = ['meal', 'fridge', 'dashboard', 'profile'];
    pages.forEach(name => {
        const page = document.getElementById(`page-${name}`);
        const nav = document.getElementById(`nav-${name}`);
        if (name === pageName) {
            page.classList.remove('hidden');
            nav.classList.add('active', 'text-primary');
        } else {
            page.classList.add('hidden');
            nav.classList.remove('active', 'text-primary');
        }
    });
    if (pageName === 'dashboard' && typeof window.__fetchDashboardMetrics === 'function') {
        window.__fetchDashboardMetrics();
    }
    if (pageName === 'meal' && typeof window.__updateFridgeSummaryBanner === 'function') {
        window.__updateFridgeSummaryBanner();
    }
    if (pageName === 'meal' && typeof window.__fetchProactiveSuggestions === 'function') {
        window.__fetchProactiveSuggestions();
    }
}

document.addEventListener("DOMContentLoaded", () => {
    // ==========================================
    // ステート管理
    // ==========================================
    const state = {
        // 認証
        token: localStorage.getItem("tomorrows_meal_token") || null,

        // プロファイル
        allergies: [],
        dislikes: [],

        // 今日の献立条件 (セッション保持)
        mealCondition: {
            cookingTime: 15,       // 分
            effortLevel: "normal", // easy / normal / hard
            moodTags: [],          // チップ選択
            moodFreetext: "",      // フリーテキスト
        },

        // 冷蔵庫で認識した食材リスト（Vision結果 / ページ内遷移でも保持）
        fridgeIngredients: [],
        // 除外された食材のインデックス（Set）
        excludedIngredientIndices: new Set(),
    };

    // 調理時間スライダーの値マップ (step -> 分表示)
    const TIME_MAP = { 1: 5, 2: 10, 3: 15, 4: 20, 5: 30, 6: 45, 7: 60, 8: "∞" };
    const TIME_VALUE_MAP = { 1: 5, 2: 10, 3: 15, 4: 20, 5: 30, 6: 45, 7: 60, 8: 999 };

    // ==========================================
    // DOM 要素の取得
    // ==========================================

    // 認証画面
    const authView = document.getElementById("auth-view");
    const appView = document.getElementById("app-view");

    // プロファイルフォーム
    const profileForm = document.getElementById("profile-form");
    const displayNameInput = document.getElementById("display-name");
    const allergyInput = document.getElementById("allergy-input");
    const addAllergyBtn = document.getElementById("add-allergy-btn");
    const allergyTagsContainer = document.getElementById("allergy-tags");
    const dislikeInput = document.getElementById("dislike-input");
    const addDislikeBtn = document.getElementById("add-dislike-btn");
    const dislikeTagsContainer = document.getElementById("dislike-tags");
    const saveProfileBtn = document.getElementById("save-profile-btn");

    // 今日の献立条件フォーム
    const cookingTimeSlider = document.getElementById("cooking-time-slider");
    const cookingTimeDisplay = document.getElementById("cooking-time-display");
    const moodFreetext = document.getElementById("mood-freetext");
    const moodCharCount = document.getElementById("mood-char-count");
    const suggestBtn = document.getElementById("suggest-btn");
    const suggestLoading = document.getElementById("suggest-loading");
    const suggestMessage = document.getElementById("suggest-message");
    const suggestMessageText = document.getElementById("suggest-message-text");
    const recipeList = document.getElementById("recipe-list");

    // 音声相談（Issue #39 / Gemini Live）
    // モーダル等の別画面は使わず、カード上の「調理中に相談する」ボタン自体を
    // 開始前/会話中でトグルする。同時に会話できるのは1件のみ。
    let voiceAskActiveRecipeId = null; // 現在会話中のレシピID（nullなら非アクティブ）
    let voiceAskConversation = null; // アクティブな音声会話セッション（VoiceConversation インスタンス）

    // 共通
    const toastContainer = document.getElementById("toast-container");
    const logoutBtn = document.getElementById("logout-btn");
    const logoutBtnProfile = document.getElementById("logout-btn-profile");
    const goalOtherWrap = document.getElementById("goal-other-input-wrap");
    const goalOtherText = document.getElementById("goal-other-text");

    // 「その他」選択時に自由記述欄を開閉
    document.querySelectorAll('input[name="goal"]').forEach((radio) => {
        radio.addEventListener("change", () => {
            if (radio.value === "other") {
                goalOtherWrap.classList.remove("hidden");
                goalOtherText.focus();
            } else {
                goalOtherWrap.classList.add("hidden");
            }
        });
    });

    const EFFORT_LABEL_MAP = {
        easy: "ラクチン",
        normal: "普通",
        hard: "本格派"
    };

    // ==========================================
    // トースト通知
    // ==========================================
    function showToast(message, type = "success") {
        const alertDiv = document.createElement("div");
        const alertClass = type === "success" ? "alert-success" : "alert-error";
        alertDiv.className = `alert ${alertClass} shadow-lg py-3 px-4 flex items-center gap-2 transition-all duration-300 transform translate-y-4 opacity-0`;
        const icon = type === "success" ? "✅" : "⚠️";
        alertDiv.innerHTML = `
            <span class="text-base">${icon}</span>
            <span class="text-sm font-bold">${message}</span>
        `;
        toastContainer.appendChild(alertDiv);
        requestAnimationFrame(() => {
            alertDiv.classList.remove("translate-y-4", "opacity-0");
        });
        setTimeout(() => {
            alertDiv.classList.add("translate-y-4", "opacity-0");
            alertDiv.addEventListener("transitionend", () => alertDiv.remove());
        }, 3000);
    }

    // ==========================================
    // 認証ヘルパー
    // ==========================================
    function getAuthHeaders() {
        return {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${state.token}`
        };
    }

    function handleUnauthorized() {
        state.token = null;
        localStorage.removeItem("tomorrows_meal_token");
        showView();
        showToast("セッションの期限が切れました。再度ログインしてください。", "error");
    }

    // ==========================================
    // Google OAuth2 ログイン処理
    // ==========================================
    async function handleGoogleCredential(response) {
        const loadingEl = document.getElementById("google-signin-loading");
        if (loadingEl) loadingEl.textContent = "認証中...";
        try {
            const res = await fetch("/api/auth/google", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id_token: response.credential })
            });
            if (!res.ok) {
                const errData = await res.json();
                throw new Error(errData.detail || "Googleログインに失敗しました。");
            }
            const data = await res.json();
            showToast("ログインしました！", "success");
            loginSuccess(data.access_token);
        } catch (error) {
            showToast(error.message, "error");
            if (loadingEl) loadingEl.textContent = "Googleアカウントで続ける...";
        }
    }

    async function initGoogleSignIn() {
        const loadingEl = document.getElementById("google-signin-loading");
        try {
            const configRes = await fetch("/api/auth/config");
            const config = await configRes.json();
            const clientId = config.google_client_id;
            if (!clientId) {
                if (loadingEl) loadingEl.textContent = "Googleログインは設定されていません。";
                return;
            }
            if (loadingEl) loadingEl.classList.add("hidden");
            // GSI スクリプトが async/defer でロードされるため、最大3秒ポーリングして待つ
            await new Promise((resolve, reject) => {
                if (typeof google !== "undefined") { resolve(); return; }
                let elapsed = 0;
                const timer = setInterval(() => {
                    elapsed += 100;
                    if (typeof google !== "undefined") { clearInterval(timer); resolve(); }
                    else if (elapsed >= 3000) { clearInterval(timer); reject(new Error("GSIライブラリの読み込みがタイムアウトしました")); }
                }, 100);
            });
            google.accounts.id.initialize({
                client_id: clientId,
                callback: handleGoogleCredential,
                auto_select: false,
            });
            const btnContainer = document.getElementById("google-signin-btn");
            const btnWidth = Math.min(btnContainer.offsetWidth || 360, 400);
            google.accounts.id.renderButton(
                btnContainer,
                { theme: "outline", size: "large", text: "signin_with", locale: "ja", width: btnWidth }
            );
        } catch (e) {
            if (loadingEl) { loadingEl.classList.remove("hidden"); loadingEl.textContent = e.message || "認証の設定取得に失敗しました。"; }
        }
    }

    // ==========================================
    // ログイン成功
    // ==========================================
    function loginSuccess(token) {
        state.token = token;
        localStorage.setItem("tomorrows_meal_token", token);
        showView();
        fetchProfile();
        fetchProactiveSuggestions();
    }

    // ==========================================
    // ログアウト処理
    // ==========================================
    function doLogout() {
        state.token = null;
        localStorage.removeItem("tomorrows_meal_token");
        showView();
        showToast("ログアウトしました。", "success");
        displayNameInput.value = "";
        state.allergies = [];
        state.dislikes = [];
        renderTags("allergy");
        renderTags("dislike");
        document.getElementById("goal-other").checked = true;
        goalOtherText.value = "";
        goalOtherWrap.classList.add("hidden");
        document.querySelectorAll('input[name="kitchen_tools"]').forEach(cb => cb.checked = false);
        dashboardLoaded = false;
        dashboardContent.classList.add("hidden");
        proactiveLoaded = false;
        proactiveSuggestions = [];
        proactiveSection.classList.add("hidden");
        proactiveList.innerHTML = "";
    }
    logoutBtn.addEventListener("click", doLogout);
    logoutBtnProfile.addEventListener("click", doLogout);

    // ==========================================
    // ビュー切り替え
    // ==========================================
    function showView() {
        if (state.token) {
            authView.classList.add("hidden");
            appView.classList.remove("hidden");
        } else {
            authView.classList.remove("hidden");
            appView.classList.add("hidden");
        }
    }

    // ==========================================
    // プロファイルタグ管理
    // ==========================================
    function renderTags(type) {
        const container = type === "allergy" ? allergyTagsContainer : dislikeTagsContainer;
        const list = type === "allergy" ? state.allergies : state.dislikes;
        container.innerHTML = "";
        list.forEach((item, index) => {
            const tag = document.createElement("div");
            tag.className = "badge border-primary bg-primary/10 text-base-content gap-1.5 p-3.5 text-sm font-medium";
            tag.innerHTML = `
                <span>${item}</span>
                <button type="button" class="btn btn-ghost btn-xs p-0 min-h-0 h-4 w-4 text-base-content/50 hover:text-error hover:bg-transparent font-bold flex items-center justify-center" data-index="${index}">&times;</button>
            `;

            tag.querySelector("button").addEventListener("click", () => {
                removeTag(type, index);
            });

            container.appendChild(tag);
        });
    }

    function addTag(type) {
        const input = type === "allergy" ? allergyInput : dislikeInput;
        const value = input.value.trim();
        if (!value) return;
        const list = type === "allergy" ? state.allergies : state.dislikes;
        if (!list.includes(value)) {
            list.push(value);
            renderTags(type);
        }
        input.value = "";
        input.focus();
    }

    function removeTag(type, index) {
        const list = type === "allergy" ? state.allergies : state.dislikes;
        list.splice(index, 1);
        renderTags(type);
    }

    addAllergyBtn.addEventListener("click", () => addTag("allergy"));
    allergyInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addTag("allergy"); } });
    addDislikeBtn.addEventListener("click", () => addTag("dislike"));
    dislikeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addTag("dislike"); } });

    // ==========================================
    // プロファイル取得 API
    // ==========================================
    async function fetchProfile() {
        if (!state.token) return;
        try {
            const response = await fetch("/api/profile", { headers: getAuthHeaders() });
            if (response.status === 401) {
                handleUnauthorized();
                return;
            }
            if (!response.ok) throw new Error("プロファイルの読み込みに失敗しました");
            const data = await response.json();
            displayNameInput.value = data.display_name || "";
            if (data.preferences) {
                state.allergies = data.preferences.allergies || [];
                state.dislikes = data.preferences.dislikes || [];
                renderTags("allergy");
                renderTags("dislike");

                // 目標の選択
                const goal = data.preferences.goal === "none" ? "diet" : (data.preferences.goal || "diet");
                const knownGoals = ["diet", "bulk", "maintain", "other"];
                if (knownGoals.includes(goal)) {
                    const goalRadio = document.getElementById(`goal-${goal}`);
                    if (goalRadio) goalRadio.checked = true;
                    if (goal === "other") goalOtherWrap.classList.remove("hidden");
                } else {
                    // 自由記述が保存されている場合
                    document.getElementById("goal-other").checked = true;
                    goalOtherText.value = goal;
                    goalOtherWrap.classList.remove("hidden");
                }

                // 調理器具の選択状態を復元
                const kitchenTools = data.preferences.kitchen_tools || [];
                document.querySelectorAll('input[name="kitchen_tools"]').forEach(cb => {
                    cb.checked = kitchenTools.includes(cb.value);
                });
                // 電子レンジ種別ラジオ
                const microwaveVal = kitchenTools.find(v => v.startsWith("microwave_")) || "microwave_none";
                const microwaveRadio = document.querySelector(`input[name="kitchen_tools_microwave"][value="${microwaveVal}"]`);
                if (microwaveRadio) microwaveRadio.checked = true;
                // オーブン種別ラジオ
                const ovenVal = kitchenTools.find(v => v.startsWith("oven_")) || "oven_none";
                const ovenRadio = document.querySelector(`input[name="kitchen_tools_oven"][value="${ovenVal}"]`);
                if (ovenRadio) ovenRadio.checked = true;
                // コンロ口数セレクト
                const portsVal = kitchenTools.find(v => v.startsWith("stove_ports_")) || "";
                const portsSelect = document.getElementById("stove-ports-select");
                if (portsSelect) portsSelect.value = portsVal;
            }
        } catch (error) {
            console.error(error);
        }
    }

    // ==========================================
    // プロファイル保存 API
    // ==========================================
    profileForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const displayName = displayNameInput.value.trim();
        const nameError = document.getElementById("name-error");
        if (!displayName) {
            displayNameInput.classList.add("input-error");
            nameError.classList.remove("hidden");
            displayNameInput.focus();
            return;
        } else {
            displayNameInput.classList.remove("input-error");
            nameError.classList.add("hidden");
        }

        const selectedTools = [];
        document.querySelectorAll('input[name="kitchen_tools"]:checked').forEach(checkbox => {
            selectedTools.push(checkbox.value);
        });
        // 電子レンジ種別ラジオ（"なし"以外のみ追加）
        const microwaveSelected = document.querySelector('input[name="kitchen_tools_microwave"]:checked');
        if (microwaveSelected && microwaveSelected.value !== "microwave_none") {
            selectedTools.push(microwaveSelected.value);
        }
        // オーブン種別ラジオ（"なし"以外のみ追加）
        const ovenSelected = document.querySelector('input[name="kitchen_tools_oven"]:checked');
        if (ovenSelected && ovenSelected.value !== "oven_none") {
            selectedTools.push(ovenSelected.value);
        }
        // コンロ口数セレクト
        const portsSelect = document.getElementById("stove-ports-select");
        if (portsSelect && portsSelect.value) {
            selectedTools.push(portsSelect.value);
        }

        // 送信データの構築
        const selectedGoal = document.querySelector('input[name="goal"]:checked').value;
        const goalValue = selectedGoal === "other"
            ? (goalOtherText.value.trim() || "other")
            : selectedGoal;
        const payload = {
            display_name: displayName,
            preferences: {
                allergies: state.allergies,
                dislikes: state.dislikes,
                goal: goalValue,
                kitchen_tools: selectedTools
            }
        };

        saveProfileBtn.disabled = true;
        saveProfileBtn.classList.add("btn-disabled");
        const spinner = document.createElement("span");
        spinner.className = "loading loading-spinner loading-sm";
        saveProfileBtn.prepend(spinner);

        try {
            const response = await fetch("/api/profile", {
                method: "PUT",
                headers: getAuthHeaders(),
                body: JSON.stringify(payload)
            });
            if (response.status === 401) {
                handleUnauthorized();
                return;
            }
            if (!response.ok) throw new Error("保存に失敗しました");
            showToast("設定を保存しました！", "success");
        } catch (error) {
            showToast(error.message || "保存中にエラーが発生しました", "error");
        } finally {
            saveProfileBtn.disabled = false;
            saveProfileBtn.classList.remove("btn-disabled");
            const sp = saveProfileBtn.querySelector(".loading-spinner");
            if (sp) sp.remove();
        }
    });

    // ==========================================
    // 今日の献立条件 - 調理時間スライダー
    // ==========================================
    cookingTimeSlider.addEventListener("input", () => {
        const step = parseInt(cookingTimeSlider.value, 10);
        const label = TIME_MAP[step];
        cookingTimeDisplay.textContent = label;
        document.getElementById("cooking-time-unit").textContent = label === "∞" ? "" : "分以内";
        state.mealCondition.cookingTime = TIME_VALUE_MAP[step];
    });

    // ==========================================
    // 冷蔵庫食材バナー（今日の献立タブ）
    // ==========================================
    const fridgeSummaryBanner = document.getElementById("fridge-summary-banner");
    const fridgeSummaryChips = document.getElementById("fridge-summary-chips");
    const fridgeSummaryClear = document.getElementById("fridge-summary-clear");

    function getActiveIngredients() {
        return state.fridgeIngredients.filter((_, i) => !state.excludedIngredientIndices.has(i));
    }

    function updateFridgeSummaryBanner() {
        const active = getActiveIngredients();
        if (active.length === 0) {
            fridgeSummaryBanner.classList.add("hidden");
            return;
        }
        fridgeSummaryChips.innerHTML = active.map(item => {
            const name = typeof item === "object" ? (item.name || "") : String(item);
            return `<span class="badge badge-sm bg-primary/15 text-primary border-primary/20 font-semibold">${escapeHtml(name)}</span>`;
        }).join("");
        fridgeSummaryBanner.classList.remove("hidden");
    }

    fridgeSummaryClear.addEventListener("click", () => {
        state.fridgeIngredients = [];
        state.excludedIngredientIndices = new Set();
        updateFridgeSummaryBanner();
    });

    window.__updateFridgeSummaryBanner = updateFridgeSummaryBanner;
    window.__state = state;
    window.__renderIngredientList = () => {
        document.getElementById("fridge-result").classList.remove("hidden");
        renderIngredientList();
    };

    // ==========================================
    // 今日の献立条件 - 手間レベル
    // ==========================================
    function updateEffortUI() {
        document.querySelectorAll('input[name="effort_level"]').forEach(radio => {
            const card = radio.nextElementSibling;
            if (radio.checked) {
                card.classList.add('border-primary', 'bg-primary/5');
                card.classList.remove('border-base-300');
            } else {
                card.classList.remove('border-primary', 'bg-primary/5');
                card.classList.add('border-base-300');
            }
        });
    }
    document.querySelectorAll('input[name="effort_level"]').forEach(radio => {
        radio.addEventListener("change", () => {
            state.mealCondition.effortLevel = radio.value;
            updateEffortUI();
        });
    });
    updateEffortUI();

    // ==========================================
    // 今日の献立条件 - ムードチップ（軸ごとに単一選択・再クリックで解除）
    // ==========================================
    document.querySelectorAll('.mood-chip input[type="radio"]').forEach(radio => {
        radio.addEventListener("click", () => {
            if (radio.dataset.wasChecked === "true") {
                radio.checked = false;
                radio.dataset.wasChecked = "false";
            } else {
                document.querySelectorAll(`.mood-chip input[name="${radio.name}"]`).forEach(r => {
                    r.dataset.wasChecked = "false";
                });
                radio.dataset.wasChecked = "true";
            }
            radio.blur();
            state.mealCondition.moodTags = Array.from(
                document.querySelectorAll('.mood-chip input[type="radio"]:checked')
            ).map(r => r.value);
        });
    });

    // ==========================================
    // 今日の献立条件 - フリーテキスト（文字カウント + ステート保存）
    // ==========================================
    const MAX_FREETEXT = 100;
    moodFreetext.addEventListener("input", () => {
        const len = moodFreetext.value.length;
        if (len > MAX_FREETEXT) {
            moodFreetext.value = moodFreetext.value.slice(0, MAX_FREETEXT);
        }
        const currentLen = moodFreetext.value.length;
        moodCharCount.textContent = `${currentLen}/${MAX_FREETEXT}`;
        moodCharCount.classList.toggle("text-error", currentLen >= MAX_FREETEXT);
        state.mealCondition.moodFreetext = moodFreetext.value;
    });

    // ==========================================
    // AIに提案してもらうボタン
    // ==========================================
    function syncMealConditionFromForm() {
        const timeStep = parseInt(cookingTimeSlider.value, 10);
        state.mealCondition.cookingTime = TIME_VALUE_MAP[timeStep];
        const effortRadio = document.querySelector('input[name="effort_level"]:checked');
        state.mealCondition.effortLevel = effortRadio ? effortRadio.value : "normal";
        state.mealCondition.moodTags = Array.from(
            document.querySelectorAll('.mood-chip input[type="radio"]:checked')
        ).map(r => r.value);
        state.mealCondition.moodFreetext = moodFreetext.value.trim();
        return timeStep;
    }

    function setSuggestLoading(isLoading) {
        suggestBtn.disabled = isLoading;
        suggestBtn.classList.toggle("btn-disabled", isLoading);
        suggestLoading.classList.toggle("hidden", !isLoading);
        suggestLoading.classList.toggle("flex", isLoading);
    }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function renderRecipeCard(recipe, index) {
        const effortLabel = EFFORT_LABEL_MAP[recipe.effort_level] || recipe.effort_level;
        const ingredients = recipe.ingredients.map(item => `<li>${escapeHtml(item)}</li>`).join("");
        const steps = recipe.steps.map(step => `<li><span class="font-bold">Step ${step.step}</span> ${escapeHtml(step.description)}</li>`).join("");
        const tags = recipe.tags.map(tag => `<span class="badge badge-outline badge-sm">${escapeHtml(tag)}</span>`).join("");

        // 候補番号バッジ（候補1 / 候補2 / 候補3）
        const candidateLabel = `<span class="badge badge-primary badge-sm font-bold">候補${index + 1}</span>`;

        return `
            <article class="recipe-card border border-base-200 bg-base-100 rounded-2xl p-4 shadow-sm" data-recipe-id="${escapeHtml(recipe.id)}">
                <div class="mb-3">${candidateLabel}</div>
                <div class="flex items-start gap-3">
                    <div class="w-12 h-12 rounded-2xl bg-primary/10 flex items-center justify-center text-3xl shrink-0">${escapeHtml(recipe.emoji)}</div>
                    <div class="min-w-0 flex-1">
                        <h3 class="text-base font-black text-base-content leading-tight">${escapeHtml(recipe.title)}</h3>
                        <p class="text-xs text-base-content/60 mt-1 leading-relaxed">${escapeHtml(recipe.description)}</p>
                    </div>
                </div>
                <div class="grid grid-cols-3 gap-2 my-4">
                    <div class="rounded-xl bg-base-200/70 p-2 text-center">
                        <div class="text-[10px] text-base-content/50 font-bold">時間</div>
                        <div class="text-sm font-black">${recipe.cooking_time}分</div>
                    </div>
                    <div class="rounded-xl bg-base-200/70 p-2 text-center">
                        <div class="text-[10px] text-base-content/50 font-bold">手間</div>
                        <div class="text-sm font-black">${escapeHtml(effortLabel)}</div>
                    </div>
                    <div class="rounded-xl bg-base-200/70 p-2 text-center">
                        <div class="text-[10px] text-base-content/50 font-bold">人数</div>
                        <div class="text-sm font-black">${recipe.servings}人分</div>
                    </div>
                </div>
                <div class="flex flex-wrap gap-1.5 mb-4">${tags}</div>
                <details class="collapse collapse-arrow bg-base-50 border border-base-200 rounded-xl mb-4">
                    <summary class="collapse-title text-sm font-bold py-3 min-h-0">材料と作り方を見る</summary>
                    <div class="collapse-content text-sm">
                        <h4 class="font-bold mb-2">材料</h4>
                        <ul class="list-disc list-inside text-base-content/75 space-y-1 mb-4">${ingredients}</ul>
                        <h4 class="font-bold mb-2">作り方</h4>
                        <ol class="space-y-2 text-base-content/75">${steps}</ol>
                        ${recipe.nutrition_note ? `<p class="mt-4 rounded-xl bg-primary/10 p-3 text-xs text-base-content/70">${escapeHtml(recipe.nutrition_note)}</p>` : ""}
                    </div>
                </details>

                <!-- 音声相談ボタン（Issue #39 / Gemini Live） -->
                <!-- 調理⇒評価の順のため、「材料と作り方を見る」の直後・評価エリアの前に置く。
                     モーダル等の別画面は挟まず、このボタン自体の見た目が
                     開始前/会話中でトグルする（他の操作＝材料確認等と並行できる）。 -->
                <button type="button" class="voice-ask-btn btn btn-outline btn-primary btn-sm h-10 w-full rounded-full font-bold mb-4" data-recipe-id="${escapeHtml(recipe.id)}">
                    <span class="voice-ask-btn-label">🎙️ 調理中に相談する</span>
                </button>

                <!-- ===== フィードバックエリア（Issue #23） ===== -->
                <div class="feedback-area border-t border-base-200 pt-4 space-y-3">
                    <!-- 調理後の星評価 -->
                    <div class="feedback-rating">
                        <p class="text-xs font-semibold text-base-content/50 mb-2">作ってみましたか？よかったら評価してください</p>
                        <div class="star-rating flex items-center gap-1" data-recipe-id="${escapeHtml(recipe.id)}">
                            ${[1, 2, 3, 4, 5].map(n => `
                                <button type="button" class="star-btn btn btn-ghost btn-circle btn-sm text-2xl leading-none p-0 min-h-0 h-10 w-10" data-star="${n}" aria-label="星${n}">☆</button>
                            `).join("")}
                        </div>
                    </div>

                    <!-- スマートチップ（星タップ直後に表示） -->
                    <div class="smart-chips hidden space-y-3">
                        <div class="flex flex-wrap gap-2 smart-chips-list"></div>
                        <div>
                            <label class="text-xs text-base-content/50 mb-1 block">その他、気づいた点があれば（任意）</label>
                            <textarea class="feedback-comment textarea textarea-bordered textarea-sm w-full bg-base-50 focus:textarea-primary resize-none text-sm" rows="2" maxlength="500" placeholder="例: もう少し塩気が欲しかった"></textarea>
                        </div>
                        <button type="button" class="feedback-submit-btn btn btn-primary btn-sm h-10 rounded-full font-bold w-full">この内容で送信する</button>
                    </div>

                    <!-- 不採用ボタン -->
                    <button type="button" class="reject-btn btn btn-ghost btn-sm h-10 w-full rounded-full font-bold text-base-content/50 hover:text-error hover:bg-error/10" data-recipe-id="${escapeHtml(recipe.id)}">
                        🚫 不採用（もう表示しない）
                    </button>
                </div>
            </article>
        `;
    }

    // レシピID → Recipeオブジェクトのキャッシュ（音声相談モーダルでmeal_planを組み立てるために使う）
    const recipeCache = {};

    function renderSuggestResult(data) {
        suggestMessageText.textContent = data.message;
        suggestMessage.classList.remove("hidden");

        const recipesToRender = data.recipes || [];
        recipesToRender.forEach(r => { recipeCache[r.id] = r; });
        recipeList.innerHTML = recipesToRender.map((r, i) => renderRecipeCard(r, i)).join("");
        recipeList.classList.toggle("hidden", recipesToRender.length === 0);
    }

    // ==========================================
    // 能動提案（Proactive / SPEC §1 Tier2 ⑤・台本S5）
    // GET /api/proactive を叩き、賞味期限・栄養調整の提案カードを表示する。
    // 既存の suggest/propose・feedback フローには手を加えず、このセクション内で完結させる。
    // 「この提案で献立をつくる」は、提案の suggest_request を通常提案と同じ /api/propose に投げ、
    // 監査ループ・層1フィルタを通した上で既存の描画関数（renderSuggestResult / setSuggestLoading）を利用する。
    // ==========================================
    const proactiveSection = document.getElementById("proactive-section");
    const proactiveList = document.getElementById("proactive-list");
    let proactiveLoaded = false;

    // trigger_type → 見出しの見せ方（アイコン・ラベル）
    const PROACTIVE_META = {
        expiring: { icon: "⏳", label: "賞味期限が近い食材の使い切り" },
        nutrition: { icon: "🥗", label: "栄養バランスの調整" },
        calendar: { icon: "📅", label: "作り置きの提案" },
    };

    function renderProactiveCard(item, index) {
        const meta = PROACTIVE_META[item.trigger_type] || { icon: "🔔", label: "AIからの提案" };
        const isHigh = item.urgency === "high" || item.trigger_type === "expiring";

        // 賞味期限・高緊急度は error 系（.badge-error / .text-error）で一瞬で警告を伝える（CLAUDE.md §4）
        const accentBadge = isHigh
            ? `<span class="badge badge-error badge-sm font-bold gap-1">⚠️ 早めに使い切り</span>`
            : `<span class="badge badge-warning badge-sm font-bold">おすすめ調整</span>`;
        const borderClass = isHigh ? "border-error/40" : "border-primary/30";
        const titleClass = isHigh ? "text-error" : "text-base-content";

        return `
            <article class="card card-bordered ${borderClass} bg-base-100 rounded-2xl p-4 shadow-sm" data-proactive-index="${index}">
                <div class="flex items-start gap-3">
                    <div class="w-10 h-10 rounded-2xl bg-base-200/70 flex items-center justify-center text-2xl shrink-0">${meta.icon}</div>
                    <div class="min-w-0 flex-1">
                        <div class="flex items-center gap-2 flex-wrap mb-1">
                            <h3 class="text-sm font-black ${titleClass} leading-tight">${escapeHtml(meta.label)}</h3>
                            ${accentBadge}
                        </div>
                        <p class="text-xs text-base-content/70 leading-relaxed">${escapeHtml(item.reason)}</p>
                    </div>
                </div>
                <button type="button" class="proactive-accept-btn btn btn-primary btn-sm h-11 w-full rounded-full font-bold mt-3 gap-1.5" data-proactive-index="${index}">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
                    この提案で献立をつくる
                </button>
            </article>
        `;
    }

    // 提案キャッシュ（「この提案で作る」で suggest_request を取り出すため）
    let proactiveSuggestions = [];

    async function acceptProactiveSuggestion(index) {
        const item = proactiveSuggestions[index];
        if (!item || !item.suggest_request) return;
        const req = item.suggest_request;

        // 既存の結果表示欄を借りて、提案の suggest_request を投げる。
        // 通常の提案ボタンと同様、能動提案からの調理も /api/propose に一本化し、
        // ADK 4エージェント＋生成⇄監査ループ＋層1決定的フィルタ（アレルギー・
        // 苦手食材・未所持調理器具）を必ず通す（台本S2/S5・SPEC §5.2）。
        suggestMessage.classList.add("hidden");
        recipeList.classList.add("hidden");
        setSuggestLoading(true);

        const formData = new FormData();
        formData.append("cooking_time", String(req.cooking_time));
        formData.append("effort_level", req.effort_level);
        formData.append("mood_tags", JSON.stringify(req.mood_tags || []));
        formData.append("mood_freetext", req.mood_freetext || "");
        formData.append("ingredients", JSON.stringify(req.ingredients || []));

        try {
            const response = await fetch("/api/propose", {
                method: "POST",
                // multipart のため Content-Type は付けない（ブラウザが boundary を付与）。
                headers: { "Authorization": `Bearer ${state.token}` },
                body: formData,
            });
            if (response.status === 401) {
                handleUnauthorized();
                return;
            }
            if (!response.ok) throw new Error("献立提案の取得に失敗しました");
            const data = await response.json();
            renderSuggestResult(data);
            recipeList.scrollIntoView({ behavior: "smooth", block: "start" });
            showToast("AIの提案から献立をつくりました。", "success");
        } catch (error) {
            if (error.message !== "認証切れ") {
                showToast(error.message || "献立提案中にエラーが発生しました", "error");
            }
        } finally {
            setSuggestLoading(false);
        }
    }

    proactiveList.addEventListener("click", (e) => {
        const btn = e.target.closest(".proactive-accept-btn");
        if (!btn) return;
        acceptProactiveSuggestion(parseInt(btn.dataset.proactiveIndex, 10));
    });

    async function fetchProactiveSuggestions(force = false) {
        if (!state.token) return;
        if (proactiveLoaded && !force) return;

        try {
            const response = await fetch("/api/proactive", { headers: getAuthHeaders() });
            if (response.status === 401) {
                // 能動提案は付加機能。ここでは強制ログアウトさせず静かに諦める。
                return;
            }
            if (!response.ok) return;

            const data = await response.json();
            proactiveSuggestions = data.suggestions || [];
            proactiveLoaded = true;

            if (proactiveSuggestions.length === 0) {
                // 発火した提案がなければセクションごと隠す（空状態は出さない）
                proactiveSection.classList.add("hidden");
                proactiveList.innerHTML = "";
                return;
            }

            proactiveList.innerHTML = proactiveSuggestions
                .map((item, i) => renderProactiveCard(item, i))
                .join("");
            proactiveSection.classList.remove("hidden");
        } catch (error) {
            // ネットワークエラー等は握りつぶす（付加機能のため他フローを妨げない）
            console.warn("能動提案の取得に失敗しました:", error);
        }
    }

    // switchPage（グローバル関数）から呼べるようにwindowへ公開
    window.__fetchProactiveSuggestions = () => fetchProactiveSuggestions(false);

    // ==========================================
    // フィードバック（Issue #23 / SPEC §5.3）
    // ==========================================
    const SMART_CHIPS = {
        low: ["工程が大変だった", "味が合わなかった", "量が多かった"],
        high: ["味付けが最高", "手軽だった", "子供が喜んだ"],
    };

    async function postFeedback(payload) {
        const response = await fetch("/api/feedback", {
            method: "POST",
            headers: getAuthHeaders(),
            body: JSON.stringify(payload),
        });
        if (response.status === 401) {
            handleUnauthorized();
            throw new Error("認証切れ");
        }
        if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.detail || "フィードバックの送信に失敗しました");
        }
        return response.json();
    }

    // ==========================================
    // 音声相談（Issue #39 / Gemini Live・Tier2加点要素）
    // ==========================================

    // レシピを3食すべてに複製し、バックエンドが要求する MealPlan 形式を組み立てる
    function buildMealPlanFromRecipe(recipe) {
        const item = { ...recipe, meal_type: "dinner" };
        return { breakfast: item, lunch: item, dinner: item };
    }

    // マイク音声キャプチャ・WebSocket中継・Gemini Live音声再生を1つにまとめたクラス。
    // ブラウザは録音・再生のみを担い、Gemini Liveとの実際の通信はバックエンドが中継する
    // （認証情報をフロントに渡さないための「ブリッジ」構成）。
    class VoiceConversation {
        constructor({ mealPlan, recipeId, onFallback, onError, onStop }) {
            this.mealPlan = mealPlan;
            this.recipeId = recipeId;
            this.onFallback = onFallback;
            this.onError = onError;
            this.onStop = onStop;
            this.ws = null;
            this.audioContext = null;
            this.micStream = null;
            this.micSourceNode = null;
            this.micProcessorNode = null;
            this.playbackQueueTime = 0;
        }

        async start() {
            this.micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)();

            const token = state.token;
            const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
            this.ws = new WebSocket(`${wsProtocol}://${window.location.host}/api/voice/session?token=${encodeURIComponent(token)}`);
            this.ws.binaryType = "arraybuffer";

            await new Promise((resolve, reject) => {
                this.ws.addEventListener("open", () => {
                    this.ws.send(JSON.stringify({
                        type: "start",
                        meal_plan: this.mealPlan,
                        recipe_id: this.recipeId,
                    }));
                    resolve();
                });
                this.ws.addEventListener("error", () => reject(new Error("音声サーバーへの接続に失敗しました")));
            });

            this.ws.addEventListener("message", (event) => this._handleServerMessage(event));
            this.ws.addEventListener("close", () => this._handleClose());

            this._startMicCapture();
        }

        _startMicCapture() {
            // Gemini Live の realtime input は 16bit PCM, 16kHz, mono を要求する。
            // ScriptProcessorNode は非推奨だが、追加ファイル(AudioWorklet)なしで
            // 全ブラウザ動作するためこの規模の実装では妥当な選択。
            const inputSampleRate = this.audioContext.sampleRate;
            const targetSampleRate = 16000;

            this.micSourceNode = this.audioContext.createMediaStreamSource(this.micStream);
            this.micProcessorNode = this.audioContext.createScriptProcessor(4096, 1, 1);

            this.micProcessorNode.onaudioprocess = (event) => {
                if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
                const inputData = event.inputBuffer.getChannelData(0);
                const downsampled = this._downsampleTo16k(inputData, inputSampleRate, targetSampleRate);
                const pcm16 = this._floatTo16BitPCM(downsampled);
                this.ws.send(pcm16);
            };

            // ScriptProcessorNode は出力先に接続しないと onaudioprocess が発火しないため、
            // 無音のダミー接続として destination に繋ぐ（マイク音声自体は再生しない）。
            this.micSourceNode.connect(this.micProcessorNode);
            this.micProcessorNode.connect(this.audioContext.destination);
        }

        _downsampleTo16k(buffer, inputSampleRate, targetSampleRate) {
            if (targetSampleRate === inputSampleRate) return buffer;
            const ratio = inputSampleRate / targetSampleRate;
            const newLength = Math.round(buffer.length / ratio);
            const result = new Float32Array(newLength);
            for (let i = 0; i < newLength; i++) {
                result[i] = buffer[Math.round(i * ratio)];
            }
            return result;
        }

        _floatTo16BitPCM(floatBuffer) {
            const pcm16 = new Int16Array(floatBuffer.length);
            for (let i = 0; i < floatBuffer.length; i++) {
                const s = Math.max(-1, Math.min(1, floatBuffer[i]));
                pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
            }
            return pcm16.buffer;
        }

        _handleServerMessage(event) {
            if (typeof event.data === "string") {
                let payload;
                try {
                    payload = JSON.parse(event.data);
                } catch (e) {
                    return;
                }
                if (payload.type === "fallback" && this.onFallback) {
                    this.onFallback(payload.message);
                }
                if (payload.type === "daily_limit") {
                    const reset = payload.reset_at ? `（リセット: ${payload.reset_at}）` : "";
                    if (this.onFallback) this.onFallback(`${payload.message}${reset}`);
                }
                return;
            }
            // Gemini Live からの音声出力（生PCM, 24kHz, mono）を再生する
            this._playAudioChunk(event.data);
        }

        async _playAudioChunk(arrayBuffer) {
            const outputSampleRate = 24000;
            const pcm16 = new Int16Array(arrayBuffer);
            const float32 = new Float32Array(pcm16.length);
            for (let i = 0; i < pcm16.length; i++) {
                float32[i] = pcm16[i] / (pcm16[i] < 0 ? 0x8000 : 0x7fff);
            }

            const audioBuffer = this.audioContext.createBuffer(1, float32.length, outputSampleRate);
            audioBuffer.copyToChannel(float32, 0);

            const sourceNode = this.audioContext.createBufferSource();
            sourceNode.buffer = audioBuffer;
            sourceNode.connect(this.audioContext.destination);

            const now = this.audioContext.currentTime;
            const startAt = Math.max(now, this.playbackQueueTime);
            sourceNode.start(startAt);
            this.playbackQueueTime = startAt + audioBuffer.duration;
        }

        _handleClose() {
            if (this.onStop) this.onStop();
        }

        stop() {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ type: "stop" }));
                this.ws.close();
            }
            this.ws = null;
            if (this.micProcessorNode) {
                this.micProcessorNode.disconnect();
                this.micProcessorNode = null;
            }
            if (this.micSourceNode) {
                this.micSourceNode.disconnect();
                this.micSourceNode = null;
            }
            if (this.micStream) {
                this.micStream.getTracks().forEach(track => track.stop());
                this.micStream = null;
            }
            if (this.audioContext) {
                this.audioContext.close();
                this.audioContext = null;
            }
        }
    }

    // 現在アクティブなボタン要素自体を「会話中」の見た目に切り替える（モーダル等の
    // 別画面は使わない。材料と作り方の確認など、他の操作と並行して続けられる）。
    function setVoiceAskBtnActiveState(btn, isActive) {
        const label = btn.querySelector(".voice-ask-btn-label");
        if (isActive) {
            label.textContent = "⏹️ 会話を終了する";
            btn.classList.remove("btn-outline", "btn-primary");
            btn.classList.add("btn-error");
        } else {
            label.textContent = "🎙️ 調理中に相談する";
            btn.classList.remove("btn-error");
            btn.classList.add("btn-outline", "btn-primary");
        }
    }

    function stopVoiceConversation() {
        if (voiceAskConversation) {
            voiceAskConversation.stop();
            voiceAskConversation = null;
        }
        if (voiceAskActiveRecipeId) {
            const activeBtn = recipeList.querySelector(`.voice-ask-btn[data-recipe-id="${voiceAskActiveRecipeId}"]`);
            if (activeBtn) setVoiceAskBtnActiveState(activeBtn, false);
        }
        voiceAskActiveRecipeId = null;
    }

    // 音声相談ボタン（カードから押した瞬間に、間の確認画面を挟まず音声Liveを開始する。
    // 会話中に同じボタンを押すと終了する）
    recipeList.addEventListener("click", async (e) => {
        const voiceAskBtn = e.target.closest(".voice-ask-btn");
        if (!voiceAskBtn) return;

        const recipeId = voiceAskBtn.dataset.recipeId;

        // 会話中に同じボタンを押した → 終了
        if (voiceAskActiveRecipeId === recipeId) {
            stopVoiceConversation();
            return;
        }

        // 別のレシピで会話中だった場合は先に終了する（同時に1件のみ）
        if (voiceAskActiveRecipeId) {
            stopVoiceConversation();
        }

        const recipe = recipeCache[recipeId];
        voiceAskActiveRecipeId = recipeId;
        setVoiceAskBtnActiveState(voiceAskBtn, true);

        try {
            voiceAskConversation = new VoiceConversation({
                mealPlan: recipe ? buildMealPlanFromRecipe(recipe) : null,
                recipeId: recipeId,
                onFallback: (message) => {
                    showToast(message, "error");
                    stopVoiceConversation();
                },
                onError: (message) => {
                    showToast(message, "error");
                },
                onStop: () => {
                    voiceAskConversation = null;
                },
            });
            await voiceAskConversation.start();
        } catch (error) {
            showToast(error.message || "マイクへのアクセスに失敗しました。ブラウザの設定をご確認ください。", "error");
            setVoiceAskBtnActiveState(voiceAskBtn, false);
            voiceAskActiveRecipeId = null;
        }
    });

    // 不採用ボタン
    recipeList.addEventListener("click", async (e) => {
        const rejectBtn = e.target.closest(".reject-btn");
        if (!rejectBtn) return;

        const card = rejectBtn.closest(".recipe-card");
        const recipeId = rejectBtn.dataset.recipeId;
        const titleEl = card.querySelector("h3");
        const recipeTitle = titleEl ? titleEl.textContent : "";

        // レシピ本文（材料・手順・元タグ）を添えて送信する。
        // サーバー側（SPEC §5.3）が料理名でなく "揚げ物" "辛い" 等の特徴タグをLLM抽出し、
        // 除外条件（Negative Tags）として学習するために使う。cache に無ければ空配列で後方互換。
        const cached = recipeCache[recipeId] || {};
        const rejectSteps = Array.isArray(cached.steps)
            ? cached.steps.map((s) => (typeof s === "string" ? s : s.description)).filter(Boolean)
            : [];

        rejectBtn.disabled = true;
        try {
            await postFeedback({
                recipe_id: recipeId,
                recipe_title: recipeTitle,
                feedback_type: "reject",
                tags: Array.isArray(cached.tags) ? cached.tags : [],
                ingredients: Array.isArray(cached.ingredients) ? cached.ingredients : [],
                steps: rejectSteps,
            });
            card.classList.add("opacity-0", "transition-opacity", "duration-300");
            setTimeout(() => card.remove(), 300);
            showToast("この献立を不採用にしました。次回の提案に反映します。", "success");
        } catch (error) {
            rejectBtn.disabled = false;
            if (error.message !== "認証切れ") {
                showToast(error.message, "error");
            }
        }
    });

    // 星評価タップ
    recipeList.addEventListener("click", (e) => {
        const starBtn = e.target.closest(".star-btn");
        if (!starBtn) return;

        const ratingContainer = starBtn.closest(".star-rating");
        const rating = parseInt(starBtn.dataset.star, 10);
        ratingContainer.dataset.selectedRating = String(rating);

        // 星の見た目を更新
        ratingContainer.querySelectorAll(".star-btn").forEach(btn => {
            const isFilled = parseInt(btn.dataset.star, 10) <= rating;
            btn.textContent = isFilled ? "★" : "☆";
            btn.classList.toggle("text-warning", isFilled);
        });

        // スマートチップをインライン表示
        const card = starBtn.closest(".recipe-card");
        const smartChipsWrap = card.querySelector(".smart-chips");
        const chipsList = card.querySelector(".smart-chips-list");
        const chipLabels = rating <= 2 ? SMART_CHIPS.low : SMART_CHIPS.high;

        chipsList.innerHTML = chipLabels.map(label => `
            <label class="feedback-chip cursor-pointer">
                <input type="checkbox" value="${escapeHtml(label)}" class="sr-only">
                <span class="feedback-chip-inner border-2 border-base-300 rounded-full px-3.5 py-1.5 text-sm font-semibold text-base-content transition-all duration-150 block">${escapeHtml(label)}</span>
            </label>
        `).join("");

        smartChipsWrap.classList.remove("hidden");
    });

    // スマートチップ選択トグル（daisyUIのmood-chipと同じ選択スタイルを踏襲）
    // 注意: chipInner（<span>）は <label> の子要素のため、クリックするとブラウザが
    // 自動的に対応する checkbox の checked をトグルする。ここで手動トグルすると
    // 二重トグルになり常に元の状態へ戻ってしまうため、見た目の同期のみ行う。
    recipeList.addEventListener("click", (e) => {
        const chipInner = e.target.closest(".feedback-chip-inner");
        if (!chipInner) return;
        const checkbox = chipInner.previousElementSibling;
        // ブラウザのデフォルト動作によるcheckbox.checkedの変化を次のタスクで反映する
        setTimeout(() => {
            chipInner.classList.toggle("chip-selected", checkbox.checked);
        }, 0);
    });

    // 調理後フィードバック送信
    recipeList.addEventListener("click", async (e) => {
        const submitBtn = e.target.closest(".feedback-submit-btn");
        if (!submitBtn) return;

        const card = submitBtn.closest(".recipe-card");
        const recipeId = card.dataset.recipeId;
        const titleEl = card.querySelector("h3");
        const recipeTitle = titleEl ? titleEl.textContent : "";
        const ratingContainer = card.querySelector(".star-rating");
        const rating = parseInt(ratingContainer.dataset.selectedRating || "0", 10);
        const selectedTags = Array.from(card.querySelectorAll(".smart-chips-list input:checked")).map(el => el.value);
        const comment = card.querySelector(".feedback-comment").value.trim();

        if (!rating) {
            showToast("星評価を選択してください。", "error");
            return;
        }

        submitBtn.disabled = true;
        try {
            await postFeedback({
                recipe_id: recipeId,
                recipe_title: recipeTitle,
                feedback_type: "cooked",
                tags: selectedTags,
                rating: rating,
                comment: comment || null,
            });
            showToast("フィードバックを送信しました。ありがとうございます！", "success");
            const smartChipsWrap = card.querySelector(".smart-chips");
            smartChipsWrap.classList.add("hidden");
        } catch (error) {
            if (error.message !== "認証切れ") {
                showToast(error.message, "error");
            }
        } finally {
            submitBtn.disabled = false;
        }
    });

    // ==========================================
    // Generative UI (A2UI) パーサ・レンダラ（Issue #41 / 加点要素）
    // ==========================================
    // SPEC.md §5.2/§6.1/§6.4: バックエンドが application/json+a2ui の DataPart を
    // JSON Lines でストリーム配信する場合に、レシピカード／スマートチップを動的に描画する。
    //
    // 最重要方針（AC最優先事項）: A2UI形式のレンダリングが失敗・非対応の場合は、
    // 例外を上位に伝播させて必ず通常の /api/suggest 相当の描画にフォールバックする。
    // ここでは「解釈できたレシピをそのまま既存の renderRecipeCard() に渡す」ことで、
    // 通常描画とA2UI描画のUIを完全に一致させ、コア機能（レシピ提案の表示・操作）を
    // 壊さないようにする。
    async function fetchSuggestViaA2ui(payload) {
        const response = await fetch("/api/suggest/a2ui", {
            method: "POST",
            headers: getAuthHeaders(),
            body: JSON.stringify(payload)
        });

        if (response.status === 401) {
            handleUnauthorized();
            throw new Error("認証切れ");
        }
        if (response.status === 429) {
            const errData = await response.json().catch(() => ({}));
            const detail = errData.detail || {};
            const msg = detail.message || "本日の上限に達しました。";
            const reset = detail.reset_at ? `（リセット: ${detail.reset_at}）` : "";
            throw new Error(`${msg}${reset}`);
        }
        if (!response.ok || !response.body) {
            throw new Error("A2UIストリームの取得に失敗しました");
        }

        const contentType = response.headers.get("Content-Type") || "";
        if (!contentType.includes("application/json+a2ui")) {
            // mimeType未宣言 = A2UI非対応レスポンス。フォールバック対象。
            throw new Error("A2UI非対応のレスポンスです");
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let message = "";
        const recipes = [];
        let sawDone = false;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            let newlineIndex;
            while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
                const line = buffer.slice(0, newlineIndex).trim();
                buffer = buffer.slice(newlineIndex + 1);
                if (!line) continue;

                // 1行でもパース不能ならフォールバックへ（AC: フォールバック最優先）
                const dataPart = JSON.parse(line);
                if (dataPart.mimeType !== "application/json+a2ui" || !dataPart.data) {
                    throw new Error("不正なA2UI DataPartを受信しました");
                }

                const component = dataPart.data.component;
                if (component === "message") {
                    message = dataPart.data.text || "";
                } else if (component === "recipe_card") {
                    recipes[dataPart.data.index] = dataPart.data.recipe;
                } else if (component === "done") {
                    sawDone = true;
                }
            }
        }

        if (!sawDone) {
            throw new Error("A2UIストリームが正常に終端しませんでした");
        }

        const cleanRecipes = recipes.filter(Boolean);
        if (cleanRecipes.length === 0) {
            throw new Error("A2UIストリームにレシピが含まれていません");
        }

        return { recipes: cleanRecipes, message };
    }

    // ==========================================
    // AIに提案してもらうボタン
    // ==========================================
    suggestBtn.addEventListener("click", async () => {
        syncMealConditionFromForm();

        suggestMessage.classList.add("hidden");
        recipeList.classList.add("hidden");
        setSuggestLoading(true);

        // SPEC §5.2 / 台本S2: 通常の提案フローは /api/propose に一本化する。
        // /api/propose は ADK 4エージェント（収集・解析・生成・監査）＋ 生成⇄監査ループ＋
        // 層1決定的フィルタ（アレルギー・苦手食材・未所持調理器具）を通す。
        // multipart/form-data で送信し、冷蔵庫写真があればそのまま渡して Vision を再解析、
        // 無ければ冷蔵庫タブで認識済みの食材（ingredients）を引き継ぐ。
        const formData = new FormData();
        formData.append("cooking_time", String(state.mealCondition.cookingTime));
        formData.append("effort_level", state.mealCondition.effortLevel);
        formData.append("mood_tags", JSON.stringify(state.mealCondition.moodTags));
        formData.append("mood_freetext", state.mealCondition.moodFreetext);
        formData.append("ingredients", JSON.stringify(getActiveIngredients()));

        // アップロード済みの冷蔵庫写真があれば同送（Vision Analyzer Agent が再解析）。
        const fridgeFile = fridgeFileInput && fridgeFileInput.files ? fridgeFileInput.files[0] : null;
        if (fridgeFile) {
            formData.append("file", fridgeFile);
        }

        try {
            const response = await fetch("/api/propose", {
                method: "POST",
                // multipart のため Content-Type は付けない（ブラウザが boundary を付与）。
                headers: { "Authorization": `Bearer ${state.token}` },
                body: formData
            });

            if (response.status === 401) {
                handleUnauthorized();
                return;
            }
            if (response.status === 429) {
                const errData = await response.json().catch(() => ({}));
                const detail = errData.detail || {};
                const msg = detail.message || "本日の上限に達しました。";
                const reset = detail.reset_at ? `（リセット: ${detail.reset_at}）` : "";
                throw new Error(`${msg}${reset}`);
            }
            if (!response.ok) throw new Error("献立提案の取得に失敗しました");

            const data = await response.json();
            renderSuggestResult(data);
            showToast("献立を提案しました。", "success");
        } catch (error) {
            if (error.message !== "認証切れ") {
                showToast(error.message || "献立提案中にエラーが発生しました", "error");
            }
        } finally {
            setSuggestLoading(false);
        }
    });

    // ==========================================
    // 冷蔵庫写真アップロード
    // ==========================================
    const fridgeFileInput = document.getElementById("fridge-file-input");
    const fridgeUploadArea = document.getElementById("fridge-upload-area");
    const fridgePlaceholder = document.getElementById("fridge-upload-placeholder");
    const fridgePreview = document.getElementById("fridge-preview");
    const fridgeAnalyzeBtn = document.getElementById("fridge-analyze-btn");
    const fridgeLoading = document.getElementById("fridge-loading");
    const fridgeResult = document.getElementById("fridge-result");
    const fridgeIngredientList = document.getElementById("fridge-ingredient-list");
    const fridgeIngredientCount = document.getElementById("fridge-ingredient-count");
    const fridgeError = document.getElementById("fridge-error");
    const fridgeErrorText = document.getElementById("fridge-error-text");

    fridgeFileInput.addEventListener("change", () => {
        const file = fridgeFileInput.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (e) => {
            fridgePreview.src = e.target.result;
            fridgePreview.classList.remove("hidden");
            fridgePlaceholder.classList.add("hidden");
        };
        reader.readAsDataURL(file);
        fridgeAnalyzeBtn.disabled = false;
        fridgeResult.classList.add("hidden");
        fridgeError.classList.add("hidden");
    });

    document.querySelectorAll(".fridge-sample-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            const src = btn.dataset.src;
            try {
                const res = await fetch(src);
                const blob = await res.blob();
                const filename = src.split("/").pop();
                const file = new File([blob], filename, { type: blob.type || "image/png" });
                const dt = new DataTransfer();
                dt.items.add(file);
                fridgeFileInput.files = dt.files;

                document.querySelectorAll(".fridge-sample-btn").forEach(b => b.classList.remove("border-primary", "ring-2", "ring-primary"));
                btn.classList.add("border-primary", "ring-2", "ring-primary");

                fridgePreview.src = src;
                fridgePreview.classList.remove("hidden");
                fridgePlaceholder.classList.add("hidden");
                fridgeAnalyzeBtn.disabled = false;
                fridgeResult.classList.add("hidden");
                fridgeError.classList.add("hidden");
            } catch (e) {
                showToast("サンプル写真の読み込みに失敗しました", "error");
            }
        });
    });

    fridgeUploadArea.addEventListener("dragover", (e) => {
        e.preventDefault();
        fridgeUploadArea.classList.add("border-primary");
    });
    fridgeUploadArea.addEventListener("dragleave", () => {
        fridgeUploadArea.classList.remove("border-primary");
    });
    fridgeUploadArea.addEventListener("drop", (e) => {
        e.preventDefault();
        fridgeUploadArea.classList.remove("border-primary");
        const file = e.dataTransfer.files[0];
        if (file) {
            fridgeFileInput.files = e.dataTransfer.files;
            fridgeFileInput.dispatchEvent(new Event("change"));
        }
    });

    const FRESHNESS_MAP = {
        good: { label: "新鮮", cls: "badge-success" },
        fair: { label: "やや古め", cls: "badge-warning" },
        poor: { label: "要注意", cls: "badge-error" },
        unknown: { label: "不明", cls: "badge-ghost" },
    };

    const fridgeRestoreBtn = document.getElementById("fridge-restore-btn");

    function renderIngredientCard(ing, index) {
        const freshness = FRESHNESS_MAP[ing.freshness] || FRESHNESS_MAP.unknown;
        const quantityText = ing.quantity != null
            ? `${ing.quantity}${escapeHtml(ing.unit)}`
            : ing.unit || "-";
        const excluded = state.excludedIngredientIndices.has(index);
        return `
            <div class="flex items-center justify-between rounded-xl px-4 py-3 border transition-all duration-150 ${excluded ? 'bg-base-200/50 border-base-200 opacity-50' : 'bg-base-50 border-base-200'}">
                <div class="flex-1 min-w-0">
                    <span class="font-bold text-sm text-base-content ${excluded ? 'line-through text-base-content/40' : ''}">${escapeHtml(ing.name)}</span>
                    <span class="text-xs text-base-content/50 ml-2">${escapeHtml(quantityText)}</span>
                </div>
                <div class="flex items-center gap-2 flex-shrink-0">
                    ${excluded
                        ? `<span class="text-xs text-base-content/40">除外中</span>`
                        : `<span class="badge ${freshness.cls} badge-sm">${freshness.label}</span>`
                    }
                    <button type="button" class="btn btn-ghost btn-xs btn-circle ingredient-toggle-btn ${excluded ? 'text-primary' : 'text-base-content/30 hover:text-error'}" data-index="${index}" title="${excluded ? '元に戻す' : '除外する'}">
                        ${excluded
                            ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>`
                            : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`
                        }
                    </button>
                </div>
            </div>
        `;
    }

    function renderIngredientList() {
        fridgeIngredientList.innerHTML = state.fridgeIngredients.map((ing, i) => renderIngredientCard(ing, i)).join("");
        const hasExcluded = state.excludedIngredientIndices.size > 0;
        fridgeRestoreBtn.classList.toggle("hidden", !hasExcluded);
        const activeCount = state.fridgeIngredients.length - state.excludedIngredientIndices.size;
        fridgeIngredientCount.textContent = state.excludedIngredientIndices.size > 0
            ? `${activeCount}/${state.fridgeIngredients.length}種類`
            : `${state.fridgeIngredients.length}種類`;
        updateFridgeSummaryBanner();
    }

    fridgeIngredientList.addEventListener("click", (e) => {
        const btn = e.target.closest(".ingredient-toggle-btn");
        if (!btn) return;
        const idx = parseInt(btn.dataset.index, 10);
        if (state.excludedIngredientIndices.has(idx)) {
            state.excludedIngredientIndices.delete(idx);
        } else {
            state.excludedIngredientIndices.add(idx);
        }
        renderIngredientList();
    });

    fridgeRestoreBtn.addEventListener("click", () => {
        state.excludedIngredientIndices = new Set();
        renderIngredientList();
    });

    fridgeAnalyzeBtn.addEventListener("click", async () => {
        const file = fridgeFileInput.files[0];
        if (!file) return;

        fridgeResult.classList.add("hidden");
        fridgeError.classList.add("hidden");
        fridgeLoading.classList.remove("hidden");
        fridgeLoading.classList.add("flex");
        fridgeAnalyzeBtn.disabled = true;

        try {
            const formData = new FormData();
            formData.append("file", file);

            const response = await fetch("/api/vision", {
                method: "POST",
                headers: { "Authorization": `Bearer ${state.token}` },
                body: formData,
            });

            if (response.status === 401) {
                handleUnauthorized();
                return;
            }

            const data = await response.json();

            if (response.status === 429) {
                const detail = data.detail || {};
                const msg = detail.message || "本日の冷蔵庫解析上限に達しました。";
                const reset = detail.reset_at ? `（リセット: ${detail.reset_at}）` : "";
                showToast(`${msg}${reset}`, "error");
                return;
            }

            if (!response.ok) {
                fridgeErrorText.textContent = data.detail || "食材の認識に失敗しました";
                fridgeError.classList.remove("hidden");
                return;
            }

            // 認識結果を state に保持（ページ内遷移をまたいでも保持し、献立提案リクエストに使う）
            state.fridgeIngredients = data.ingredients;
            state.excludedIngredientIndices = new Set();
            renderIngredientList();

            fridgeResult.classList.remove("hidden");
            showToast(`${data.ingredients.length}種類の食材を認識しました！今日の献立タブで確認できます`, "success");
        } catch (error) {
            fridgeErrorText.textContent = error.message || "エラーが発生しました";
            fridgeError.classList.remove("hidden");
        } finally {
            fridgeLoading.classList.add("hidden");
            fridgeLoading.classList.remove("flex");
            fridgeAnalyzeBtn.disabled = false;
        }
    });

    // ==========================================
    // アウトカム・ダッシュボード（Issue #37）
    // ==========================================
    const dashboardLoading = document.getElementById("dashboard-loading");
    const dashboardError = document.getElementById("dashboard-error");
    const dashboardErrorText = document.getElementById("dashboard-error-text");
    const dashboardContent = document.getElementById("dashboard-content");
    const qualityChartEmpty = document.getElementById("quality-chart-empty");
    const qualityChartSvg = document.getElementById("quality-chart-svg");
    const qualityChartAverage = document.getElementById("quality-chart-average");

    let dashboardLoaded = false;

    function formatSecondsAsDuration(seconds) {
        const s = Math.round(seconds);
        if (s < 60) return `${s}秒`;
        const minutes = Math.floor(s / 60);
        const remSeconds = s % 60;
        if (minutes < 60) {
            return remSeconds > 0 ? `${minutes}分${remSeconds}秒` : `${minutes}分`;
        }
        const hours = Math.floor(minutes / 60);
        const remMinutes = minutes % 60;
        return `${hours}時間${remMinutes}分`;
    }

    function renderScalarMetric(metric, valueEl, noteEl, formatValue) {
        if (!metric || !metric.has_data) {
            valueEl.textContent = "データ蓄積中";
            valueEl.classList.add("text-base-content/40");
            valueEl.classList.remove("text-primary");
            noteEl.textContent = "実データが揃うと表示されます";
            return;
        }
        valueEl.classList.remove("text-base-content/40");
        valueEl.classList.add("text-primary");
        valueEl.textContent = formatValue(metric.value);
        noteEl.textContent = `サンプル数: ${metric.sample_size}件`;
    }

    function renderQualityScoreChart(trend) {
        if (!trend || !trend.has_data || trend.points.length === 0) {
            qualityChartEmpty.classList.remove("hidden");
            qualityChartEmpty.classList.add("flex");
            qualityChartSvg.classList.add("hidden");
            qualityChartAverage.classList.add("hidden");
            return;
        }

        qualityChartEmpty.classList.add("hidden");
        qualityChartEmpty.classList.remove("flex");
        qualityChartSvg.classList.remove("hidden");
        qualityChartAverage.classList.remove("hidden");

        const points = trend.points;
        const width = 300;
        const height = 120;
        const padding = 10;
        const scores = points.map(p => p.score);
        const minScore = Math.min(...scores, 0);
        const maxScore = Math.max(...scores, 1);
        const range = maxScore - minScore || 1;

        const coords = points.map((p, i) => {
            const x = points.length === 1
                ? width / 2
                : padding + (i / (points.length - 1)) * (width - padding * 2);
            const y = height - padding - ((p.score - minScore) / range) * (height - padding * 2);
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        });

        const polyline = coords.join(" ");
        const primaryColor = "oklch(var(--p))";

        qualityChartSvg.innerHTML = `
            <polyline points="${polyline}" fill="none" stroke="${primaryColor}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
            ${coords.map(c => {
                const [x, y] = c.split(",");
                return `<circle cx="${x}" cy="${y}" r="3" fill="${primaryColor}" />`;
            }).join("")}
        `;

        qualityChartAverage.textContent = `平均スコア: ${trend.average} (${trend.sample_size}件)`;
    }

    function renderDashboard(data) {
        renderScalarMetric(
            data.food_waste_reduction_rate,
            document.getElementById("metric-food-waste-value"),
            document.getElementById("metric-food-waste-note"),
            (v) => `${v}%`
        );
        renderScalarMetric(
            data.nutrition_goal_achievement_rate,
            document.getElementById("metric-nutrition-value"),
            document.getElementById("metric-nutrition-note"),
            (v) => `${v}%`
        );
        renderScalarMetric(
            data.decision_time,
            document.getElementById("metric-decision-time-value"),
            document.getElementById("metric-decision-time-note"),
            formatSecondsAsDuration
        );
        renderScalarMetric(
            data.cooking_time,
            document.getElementById("metric-cooking-time-value"),
            document.getElementById("metric-cooking-time-note"),
            formatSecondsAsDuration
        );
        renderQualityScoreChart(data.quality_score_trend);
    }

    async function fetchDashboardMetrics(force = false) {
        if (!state.token) return;
        if (dashboardLoaded && !force) return;

        dashboardLoading.classList.remove("hidden");
        dashboardError.classList.add("hidden");
        dashboardContent.classList.add("hidden");

        try {
            const response = await fetch("/api/metrics", { headers: getAuthHeaders() });
            if (response.status === 401) {
                handleUnauthorized();
                return;
            }
            if (!response.ok) throw new Error("指標の取得に失敗しました");

            const data = await response.json();
            renderDashboard(data);
            dashboardContent.classList.remove("hidden");
            dashboardLoaded = true;
        } catch (error) {
            dashboardErrorText.textContent = error.message || "指標の取得に失敗しました";
            dashboardError.classList.remove("hidden");
        } finally {
            dashboardLoading.classList.add("hidden");
        }
    }

    // switchPage（グローバル関数）から呼べるようにwindowへ公開
    window.__fetchDashboardMetrics = () => fetchDashboardMetrics(false);

    // ==========================================
    // 初期化
    // ==========================================
    showView();
    fetchProfile();
    fetchProactiveSuggestions();
    if (!state.token) {
        initGoogleSignIn();
    }
});
