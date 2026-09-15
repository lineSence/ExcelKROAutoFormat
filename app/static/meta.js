// Форма данных сверки: строки продавцов и ревизоров, режим распределения
// недостачи и поле ФИО ночного продавца.
(function () {
	"use strict"

	var autosaveTimer = null

	function metaForm() {
		return document.querySelector("form[action^='/meta/']")
	}

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
		queueAutosave()
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
			queueAutosave()
			return
		}
		row.remove()
		queueAutosave()
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

	function queueAutosave() {
		var form = metaForm()
		if (!form) return
		window.clearTimeout(autosaveTimer)
		autosaveTimer = window.setTimeout(function () {
			var action = form.getAttribute("action") || ""
			var marker = "/meta/"
			var start = action.indexOf(marker)
			if (start < 0) return
			var token = action.slice(start + marker.length).split("/")[0]
			if (!token) return
			var data = new FormData(form)
			// Autosave не отправляет основную форму и не запускает пересборку.
			// Он только обновляет SheetMeta в текущей Job. Следующее действие,
			// которому действительно нужен новый xlsx, возьмёт уже сохранённые поля.
			fetch("/meta/" + encodeURIComponent(token) + "/autosave", {
				method: "POST",
				body: data,
				credentials: "same-origin",
				headers: {"X-Requested-With": "XMLHttpRequest"}
			}).catch(function () {
				// Потеря одного запроса не считается ошибкой страницы: любое
				// следующее изменение формы повторит autosave с актуальным состоянием.
			})
		}, 500)
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

	document.addEventListener("input", function (event) {
		if (event.target.closest("form[action^='/meta/']")) queueAutosave()
	})

	document.addEventListener("change", function (event) {
		if (event.target.name === "share_mode") syncShareMode()
		if (event.target.hasAttribute("data-night-toggle")) syncNight()
		if (event.target.closest("form[action^='/meta/']")) queueAutosave()
	})

	syncShareMode()
	syncNight()
})()
