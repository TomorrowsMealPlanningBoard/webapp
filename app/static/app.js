// ==========================================
// ページ切り替え（グローバル関数 - HTML の onclick から呼ばれる）
// ==========================================
function switchPage(pageName) {
    const pages = ['meal', 'profile'];
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
        }
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
    const tabLogin = document.getElementById("tab-login");
    const tabRegister = document.getElementById("tab-register");
    const loginForm = document.getElementById("login-form");
    const registerForm = document.getElementById("register-form");

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
    // ログイン/登録タブ切り替え
    // ==========================================
    tabLogin.addEventListener("click", () => {
        tabLogin.classList.add("tab-active");
        tabRegister.classList.remove("tab-active");
        loginForm.classList.remove("hidden");
        registerForm.classList.add("hidden");
    });

    tabRegister.addEventListener("click", () => {
        tabRegister.classList.add("tab-active");
        tabLogin.classList.remove("tab-active");
        registerForm.classList.remove("hidden");
        loginForm.classList.add("hidden");
    });

    // ==========================================
    // ログイン処理
    // ==========================================
    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const email = document.getElementById("login-email").value.trim();
        const password = document.getElementById("login-password").value;
        const submitBtn = document.getElementById("login-submit-btn");

        if (!email || !password) {
            showToast("メールアドレスとパスワードを入力してください。", "error");
            return;
        }

        submitBtn.disabled = true;
        const spinner = document.createElement("span");
        spinner.className = "loading loading-spinner loading-sm mr-2";
        submitBtn.prepend(spinner);

        try {
            // FastAPI の OAuth2PasswordRequestForm は form-data 形式
            const formData = new URLSearchParams();
            formData.append("username", email);
            formData.append("password", password);

            const response = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: formData.toString()
            });

            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.detail || "ログインに失敗しました。");
            }

            const data = await response.json();
            showToast("ログインしました！", "success");
            loginSuccess(data.access_token);
        } catch (error) {
            showToast(error.message, "error");
        } finally {
            submitBtn.disabled = false;
            const sp = submitBtn.querySelector(".loading-spinner");
            if (sp) sp.remove();
        }
    });

    // ==========================================
    // 会員登録処理
    // ==========================================
    registerForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const displayName = document.getElementById("register-display-name").value.trim();
        const email = document.getElementById("register-email").value.trim();
        const password = document.getElementById("register-password").value;
        const submitBtn = document.getElementById("register-submit-btn");

        if (!email || !password) {
            showToast("メールアドレスとパスワードを入力してください。", "error");
            return;
        }
        if (password.length < 6) {
            showToast("パスワードは6文字以上で入力してください。", "error");
            return;
        }

        submitBtn.disabled = true;
        const spinner = document.createElement("span");
        spinner.className = "loading loading-spinner loading-sm mr-2";
        submitBtn.prepend(spinner);

        try {
            const response = await fetch("/api/auth/register", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password, display_name: displayName || null })
            });

            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.detail || "登録に失敗しました。");
            }

            const data = await response.json();
            showToast("アカウントを作成しました！", "success");
            loginSuccess(data.access_token);
        } catch (error) {
            showToast(error.message, "error");
        } finally {
            submitBtn.disabled = false;
            const sp = submitBtn.querySelector(".loading-spinner");
            if (sp) sp.remove();
        }
    });

    // ==========================================
    // ログイン成功
    // ==========================================
    function loginSuccess(token) {
        state.token = token;
        localStorage.setItem("tomorrows_meal_token", token);
        showView();
        fetchProfile();
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
            tag.className = "badge badge-neutral badge-outline gap-1.5 p-3.5 text-sm font-medium";
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
                const goal = data.preferences.goal || "other";
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
    // 今日の献立条件 - 手間レベル
    // ==========================================
    document.querySelectorAll('input[name="effort_level"]').forEach(radio => {
        radio.addEventListener("change", () => {
            state.mealCondition.effortLevel = radio.value;
        });
    });

    // ==========================================
    // 今日の献立条件 - ムードチップ（複数選択）
    // ==========================================
    document.querySelectorAll('#mood-chips input[type="checkbox"]').forEach(checkbox => {
        checkbox.addEventListener("change", () => {
            if (checkbox.checked) {
                if (!state.mealCondition.moodTags.includes(checkbox.value)) {
                    state.mealCondition.moodTags.push(checkbox.value);
                }
            } else {
                state.mealCondition.moodTags = state.mealCondition.moodTags.filter(v => v !== checkbox.value);
            }
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
            document.querySelectorAll('#mood-chips input[type="checkbox"]:checked')
        ).map(checkbox => checkbox.value);
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

    function renderRecipeCard(recipe) {
        const effortLabel = EFFORT_LABEL_MAP[recipe.effort_level] || recipe.effort_level;
        const ingredients = recipe.ingredients.map(item => `<li>${escapeHtml(item)}</li>`).join("");
        const steps = recipe.steps.map(step => `<li><span class="font-bold">Step ${step.step}</span> ${escapeHtml(step.description)}</li>`).join("");
        const tags = recipe.tags.map(tag => `<span class="badge badge-outline badge-sm">${escapeHtml(tag)}</span>`).join("");

        return `
            <article class="border border-base-200 bg-base-100 rounded-2xl p-4 shadow-sm">
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
                <details class="collapse collapse-arrow bg-base-50 border border-base-200 rounded-xl">
                    <summary class="collapse-title text-sm font-bold py-3 min-h-0">材料と作り方を見る</summary>
                    <div class="collapse-content text-sm">
                        <h4 class="font-bold mb-2">材料</h4>
                        <ul class="list-disc list-inside text-base-content/75 space-y-1 mb-4">${ingredients}</ul>
                        <h4 class="font-bold mb-2">作り方</h4>
                        <ol class="space-y-2 text-base-content/75">${steps}</ol>
                        ${recipe.nutrition_note ? `<p class="mt-4 rounded-xl bg-primary/10 p-3 text-xs text-base-content/70">${escapeHtml(recipe.nutrition_note)}</p>` : ""}
                    </div>
                </details>
            </article>
        `;
    }

    function renderSuggestResult(data) {
        suggestMessageText.textContent = data.message;
        suggestMessage.classList.remove("hidden");

        recipeList.innerHTML = data.recipes.map(renderRecipeCard).join("");
        recipeList.classList.toggle("hidden", data.recipes.length === 0);
    }

    // ==========================================
    // AIに提案してもらうボタン
    // ==========================================
    suggestBtn.addEventListener("click", async () => {
        syncMealConditionFromForm();

        suggestMessage.classList.add("hidden");
        recipeList.classList.add("hidden");
        setSuggestLoading(true);

        try {
            const response = await fetch("/api/suggest", {
                method: "POST",
                headers: getAuthHeaders(),
                body: JSON.stringify({
                    cooking_time: state.mealCondition.cookingTime,
                    effort_level: state.mealCondition.effortLevel,
                    mood_tags: state.mealCondition.moodTags,
                    mood_freetext: state.mealCondition.moodFreetext
                })
            });

            if (response.status === 401) {
                handleUnauthorized();
                return;
            }
            if (!response.ok) throw new Error("献立提案の取得に失敗しました");

            const data = await response.json();
            renderSuggestResult(data);
            showToast("モック献立を提案しました。", "success");
        } catch (error) {
            showToast(error.message || "献立提案中にエラーが発生しました", "error");
        } finally {
            setSuggestLoading(false);
        }
    });

    // ==========================================
    // 初期化
    // ==========================================
    showView();
    fetchProfile();
});
