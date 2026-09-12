// Форма данных сверки: строки продавцов и ревизоров, режим распределения
// недостачи и поле ФИО ночного продавца.
(function () {
	"use strict"

	function addRow(name) {
		var list = document.querySelector('[data-rows="' + name + '"]')
		if (!list) return
		var rows = list.querySelectorAll("[data-row]")
		if (!rows.length) return
		var copy = rows[rows.length - 1].cloneNode(true)
		Array.prototype.forEach.call(copy.querySelectorAll("input"), function (input) {
			input.value = ""
		})
		list.appendChild(copy)
		syncShareMode()
		var first = copy.querySelector("input")
		if (first) first.focus()
	}

	function removeRow(button) {
		var row = button.closest("[data-row]")
		if (!row) return
		var list = row.parentElement
		// Последнюю строку не убираем, а очищаем.
		if (list && list.querySelectorAll("[data-row]").length <= 1) {
			Array.prototype.forEach.call(row.querySelectorAll("input"), function (input) {
				input.value = ""
			})
			return
		}
		row.remove()
	}

	// В режиме «поровну» часы не участвуют в расчёте, но остаются в форме:
	// ставим readonly, а не disabled, иначе браузер не отправит их значения.
	function syncShareMode() {
		var box = document.querySelector("[data-share-mode]")
		if (!box) return
		var checked = box.querySelector("input:checked")
		var byHours = !checked || checked.value === "hours"
		Array.prototype.forEach.call(document.querySelectorAll("[data-hours]"), function (input) {
			input.readOnly = !byHours
			input.tabIndex = byHours ? 0 : -1
		})
		var hint = document.querySelector("[data-share-hint]")
		if (hint) {
			hint.textContent = byHours
				? "Недостача делится пропорционально отработанным часам каждого продавца."
				: "Недостача делится на всех продавцов поровну, часы не учитываются."
		}
	}

	function syncNight() {
		var toggle = document.querySelector("[data-night-toggle]")
		var name = document.querySelector("[data-night-name]")
		if (!toggle || !name) return
		name.disabled = !toggle.checked
		if (!toggle.checked) name.value = ""
	}

	document.addEventListener("click", function (event) {
		var add = event.target.closest("[data-row-add]")
		if (add) {
			event.preventDefault()
			addRow(add.getAttribute("data-row-add"))
			return
		}
		var remove = event.target.closest("[data-row-remove]")
		if (remove) {
			event.preventDefault()
			removeRow(remove)
		}
	})

	// Enter в строке списка добавляет следующую строку, а не отправляет форму.
	document.addEventListener("keydown", function (event) {
		if (event.key !== "Enter") return
		var row = event.target.closest("[data-row]")
		if (!row) return
		var list = row.parentElement
		if (!list || !list.hasAttribute("data-rows")) return
		event.preventDefault()
		addRow(list.getAttribute("data-rows"))
	})

	document.addEventListener("change", function (event) {
		if (event.target.name === "share_mode") syncShareMode()
		if (event.target.hasAttribute("data-night-toggle")) syncNight()
	})

	syncShareMode()
	syncNight()
})()
