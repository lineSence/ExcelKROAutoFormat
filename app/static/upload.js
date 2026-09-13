// Загрузка файла через память браузера.
//
// Обычная отправка формы читает файл с диска в момент отправки. Если Excel держит
// файл открытым или его пересохранили после выбора, браузер рвёт запрос с ошибкой
// «File changed» (ERR_UPLOAD_FILE_CHANGED) и страница вылетает. Поэтому сначала читаем
// содержимое в память, а потом отправляем копию.
//
// Если браузер старый или что-то пошло не так, форма отправляется обычным способом.
(function () {
	"use strict";

	var supported =
		typeof window.fetch === "function" &&
		typeof window.FormData === "function" &&
		typeof window.File === "function" &&
		typeof window.Blob === "function" &&
		File.prototype &&
		typeof File.prototype.arrayBuffer === "function";

	if (!supported) {
		return;
	}

	function showPage(html, url) {
		try {
			if (url && window.history && typeof window.history.replaceState === "function") {
				window.history.replaceState(null, "", url);
			}
		} catch (error) {
			// адрес в строке — не главное
		}
		document.open();
		document.write(html);
		document.close();
	}

	function lockForm(form, locked) {
		var buttons = form.querySelectorAll("button, input[type=submit]");
		for (var i = 0; i < buttons.length; i += 1) {
			buttons[i].disabled = locked;
		}
	}

	function bind(form) {
		form.addEventListener("submit", function (event) {
			var input = form.querySelector('input[type="file"]');
			if (!input || !input.files || input.files.length !== 1) {
				return;
			}

			event.preventDefault();
			var chosen = input.files[0];
			lockForm(form, true);

			chosen
				.arrayBuffer()
				.then(function (buffer) {
					if (!buffer || buffer.byteLength === 0) {
						throw new Error("empty");
					}
					var data = new FormData(form);
					var copy = new Blob([buffer], {
						type: chosen.type || "application/octet-stream",
					});
					data.set(input.name, copy, chosen.name);
					return fetch(form.action, {
						method: (form.method || "post").toUpperCase(),
						body: data,
						credentials: "same-origin",
					});
				})
				.then(function (response) {
					return response.text().then(function (html) {
						showPage(html, response.url);
					});
				})
				.catch(function () {
					// Последняя попытка: обычная отправка формы.
					lockForm(form, false);
					form.removeAttribute("data-inmemory");
					form.submit();
				});
		});
	}

	var forms = document.querySelectorAll("form[data-inmemory]");
	for (var i = 0; i < forms.length; i += 1) {
		bind(forms[i]);
	}
})();
