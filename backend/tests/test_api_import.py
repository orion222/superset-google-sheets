def test_api_module_imports():
    from michael_s_molina.sqllab_gsheets import api

    assert api.GSheetsExportAPI


def test_chart_api_module_imports():
    from michael_s_molina.sqllab_gsheets import chart_api

    assert chart_api.ChartExportAPI.class_permission_name == "GoogleSheets"
