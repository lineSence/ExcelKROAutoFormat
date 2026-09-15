def test_parse_finds_groups(source_file: Path, tmp_path: Path) -> None:
    repaired = repair_by_inject(source_file, tmp_path / "r.xlsx"); sheet = openpyxl.load_workbook(repaired)["TDSheet"]; document = parse(sheet); assert document.doc_date == "09.09.2026"; assert len(document.groups) == len(GROUPS)

def test_process_makes_output(source_file: Path, tmp_path: Path) -> None:
    settings = Settings.load(); settings.tmp_dir = str(tmp_path / "work"); result = process(source_file, source_file.name, settings)
    assert result.output_name == "ОхтаМоллСМА 09.09.2026.xlsx"; assert result.output_path.is_file(); assert result.summary["groups"] == len(GROUPS); assert result.summary["pieces"] > 0
    sheet = openpyxl.load_workbook(result.output_path)["TDSheet"]
    assert sheet["B1"].value == "Неучтёнка"; assert sheet["B2"].value == "Неподтверждённая неучтёнка"; assert sheet["C2"].value is None
    assert sheet["E1"].value == f"Найденный товар принимается до:{_accept_text()}"
    assert sheet["I5"].value == "=I4+C1"
    assert sheet["I6"].value is None
    assert sheet["G5"].value == "Недостача:"; assert sheet["G6"].value == "С неучтёнкой:"; assert sheet["B5"].value == "Склад:"; assert sheet["A5"].value is None; assert sheet["B6"].value == "Причина инвентаризации:"; assert sheet["C5"].value == "ОхтаМоллСМА"; assert sheet.auto_filter.ref.startswith("K1:K")

def test_process_uses_given_folder(source_file: Path, tmp_path: Path) -> None:
    settings = Settings.load(); settings.tmp_dir = str(tmp_path / "work"); folder = Path(settings.tmp_dir) / "one"; result = process(source_file, source_file.name, settings, folder=folder)
    assert result.output_path.parent == folder; assert [item.name for item in Path(settings.tmp_dir).iterdir()] == ["one"]

def test_sweep_removes_old_folders(tmp_path: Path) -> None:
    settings = Settings.load(); settings.tmp_dir = str(tmp_path / "work"); settings.result_ttl_minutes = 1; old = Path(settings.tmp_dir) / "old"; fresh = Path(settings.tmp_dir) / "fresh"; old.mkdir(parents=True); fresh.mkdir(parents=True); past = time.time() - 3600; os.utime(old, (past, past)); assert sweep(settings) == 1; assert not old.exists(); assert fresh.exists()