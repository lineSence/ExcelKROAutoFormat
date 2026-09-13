/*
Разбор неопределённостей: счётчик решений, групповые кнопки,
фильтр «только нерешённые» и горячие клавиши.

Страница остаётся работоспособной без этого файла: форма — обычные radio.
*/
(function () {
	"use strict";

	var root = document.querySelector("[data-review]");
	if (!root) {
		return;
	}

	var cards = Array.prototype.slice.call(root.querySelectorAll("[data-pair]"));
	if (!cards.length) {
		return;
	}

	var counter = root.querySelector("[data-review-count]");
	var submit = root.querySelector("[data-review-submit]");
	var onlyOpen = root.querySelector("[data-review-filter]");
	var total = cards.length;
	var focused = 0;

	function stateOf(card) {
		var checked = card.querySelector("input[type=radio]:checked");
		return checked ? checked.value : "skip";
	}

	function paint(card) {
		var state = stateOf(card);
		card.setAttribute("data-state", state);
		Array.prototype.forEach.call(card.querySelectorAll(".segmented label"), function (label) {
			var input = label.querySelector("input[type=radio]");
			label.classList.remove("checked-yes", "checked-no", "checked-skip");
			if (input && input.checked) {
				label.classList.add("checked-" + input.value);
			}
		});
	}

	function applyFilter() {
		var hide = onlyOpen && onlyOpen.checked;
		cards.forEach(function (card) {
			var done = stateOf(card) !== "skip";
			card.hidden = Boolean(hide && done);
		});
	}

	function refresh() {
		var decided = 0;
		cards.forEach(function (card) {
			paint(card);
			if (stateOf(card) !== "skip") {
				decided += 1;
			}
		});
		if (counter) {
			counter.textContent = decided + " из " + total;
		}
		if (submit) {
			submit.disabled = decided === 0;
		}
	}

	function setAll(value) {
		cards.forEach(function (card) {
			if (card.hidden) {
				return;
			}
			var input = card.querySelector('input[type=radio][value="' + value + '"]');
			if (input) {
				input.checked = true;
			}
		});
		refresh();
		applyFilter();
	}

	function focusCard(index) {
		var visible = cards.filter(function (card) {
			return !card.hidden;
		});
		if (!visible.length) {
			return;
		}
		focused = Math.max(0, Math.min(index, visible.length - 1));
		var card = visible[focused];
		cards.forEach(function (item) {
			item.classList.toggle("pair-focused", item === card);
		});
		card.scrollIntoView({ block: "center", behavior: "smooth" });
	}

	function answerFocused(value) {
		var visible = cards.filter(function (card) {
			return !card.hidden;
		});
		var card = visible[focused];
		if (!card) {
			return;
		}
		var input = card.querySelector('input[type=radio][value="' + value + '"]');
		if (input) {
			input.checked = true;
		}
		refresh();
		applyFilter();
		focusCard(focused + (onlyOpen && onlyOpen.checked ? 0 : 1));
	}

	root.addEventListener("change", function (event) {
		var target = event.target;
		if (target && target.matches("input[type=radio]")) {
			refresh();
			applyFilter();
		} else if (target === onlyOpen) {
			applyFilter();
			focusCard(0);
		}
	});

	Array.prototype.forEach.call(root.querySelectorAll("[data-review-all]"), function (button) {
		button.addEventListener("click", function () {
			setAll(button.getAttribute("data-review-all"));
		});
	});

	document.addEventListener("keydown", function (event) {
		if (event.ctrlKey || event.altKey || event.metaKey) {
			return;
		}
		var tag = (event.target && event.target.tagName) || "";
		if (tag === "INPUT" && event.target.type !== "radio") {
			return;
		}
		if (tag === "TEXTAREA" || tag === "SELECT") {
			return;
		}
		if (event.key === "1") {
			answerFocused("yes");
		} else if (event.key === "2") {
			answerFocused("no");
		} else if (event.key === "3") {
			answerFocused("skip");
		} else if (event.key === "j" || event.key === "ArrowDown") {
			focusCard(focused + 1);
		} else if (event.key === "k" || event.key === "ArrowUp") {
			focusCard(focused - 1);
		} else {
			return;
		}
		event.preventDefault();
	});

	refresh();
	applyFilter();
	focusCard(0);
})();
