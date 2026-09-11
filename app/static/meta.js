// Форма данных сверки: строки продавцов и ревизоров, режим распределения,
// поле ФИО ночного продавца.
(function () {
	"use strict"

	function blankRow(list) {
		var rows = list.querySelectorAll("[data-row]")
		if (!rows.length) return null
		var copy = rows[rows.length - 1].cloneNode(true)
		copy.querySelectorAll("input").forEach(function (input) {
			input.value = ""
		})
		return copy
	}

	function addRow(name) {
		var list = document.querySelector('[data-rows="' + name + '"]')
		if (!list) return
		var row = blankRow(list)
		if (!row) return
		list.appendChild(row)
		var first = row.querySelector("input")
		if (first) first.focus()
	}

	function removeRow(button) {
		var row = button.closest("[data-row]")
		if (!row) return
		var list = row.parentElement
		// Последнюю строку не убираем, а очищаем.
		if (list && list.querySelectorAll("[data-row]").length <= 1) {
			row.querySelectorAll("input").forEach(function (input) {
				input.value = ""
			})
			return
		}
		row.remove()
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

	// Режим распределения: при делении поровну часы не нужны.
	;(function () {
		var box = document.querySelector("[data-share-mode]")
		var hint = document.querySelector("[data-share-hint]")
		if (!box) return
		var sync = function () {
			var checked = box.querySelector("input:checked")
			var byHours = !checked || checked.value === "hours"
			document.querySelectorAll("[data-hours]").forEach(function (input) {
				input.disabled = !byHours
				input.placeholder = byHours ? "часы" : "—"
			})
			if (hint) {
				hint.textContent = byHours
					? "Недостача делится пропорционально отработанным часам каждого продавца."
					: "Недостача делится на всех продавцов поровну, часы не учитываются."
			}
		}
		box.addEventListener("change", sync)
		document.addEventListener("click", function () {
			setTimeout(sync, 0)
		})
		sync()
	})()

	// Поле ФИО ночного продавца нужно только при ответе «да».
	;(function () {
		var toggle = document.querySelector("[data-night-toggle]")
		var name = document.querySelector("[data-night-name]")
		if (!toggle || !name) return
		var sync = function () {
			name.disabled = !toggle.checked
			if (!toggle.checked) name.value = ""
		}
		toggle.addEventListener("change", sync)
		sync()
	})()
})()
