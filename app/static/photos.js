/*
Ответы «Да»/«Нет» по карточкам снимков без перезагрузки страницы.

Каждая карточка — обычная форма на /vision/answer. Без этого файла
страница остаётся работоспособной: форма уходит обычным POST и раздел
перерисовывается целиком.
*/
(function () {
	"use strict";

	var root = document.querySelector("[data-photo-grid]");
	if (!root || !window.fetch || !window.FormData) {
		return;
	}

	function buttons(card) {
		return card ? Array.prototype.slice.call(card.querySelectorAll("button")) : [];
	}

	function lock(card, state) {
		buttons(card).forEach(function (button) {
			button.disabled = state;
		});
	}

	function note(card, text, ok) {
		var box = card ? card.querySelector("[data-photo-note]") : null;
		if (!box) {
			return;
		}
		box.textContent = text || "";
		box.hidden = !text;
		box.classList.toggle("alert-warn", !ok);
	}

	root.addEventListener("submit", function (event) {
		var form = event.target;
		if (!form || !form.matches("[data-photo-form]")) {
			return;
		}
		event.preventDefault();
		var card = form.closest("[data-photo-card]");
		lock(card, true);
		note(card, "Записываю ответ…", true);
		fetch(form.action, {
			method: "POST",
			headers: { Accept: "application/json" },
			body: new FormData(form)
		})
			.then(function (response) {
				return response.json().catch(function () {
					return { ok: false, message: "Ответ службы не разобран." };
				});
			})
			.then(function (data) {
				if (!data || !data.ok) {
					throw new Error((data && data.message) || "Ответ не записан.");
				}
				if (card) {
					card.setAttribute("data-state", data.state || "yes");
					card.classList.add("photo-card-done");
				}
				note(card, data.message || "Ответ записан.", true);
			})
			.catch(function (error) {
				lock(card, false);
				note(card, error.message, false);
			});
	});
})();
