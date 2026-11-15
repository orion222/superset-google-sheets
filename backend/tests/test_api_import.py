def test_api_module_imports():
    from michael_s_molina.sqllab_gsheets import api

    assert api.GSheetsExportAPI


def test_chart_routes_share_the_export_api():
    from michael_s_molina.sqllab_gsheets.api import GSheetsExportAPI
    from michael_s_molina.sqllab_gsheets import chart_api

    assert GSheetsExportAPI.class_permission_name == "GoogleSheets"
    assert GSheetsExportAPI.export_chart_to_sheets
    assert GSheetsExportAPI.chart_capabilities
    assert chart_api.ChartExportRequest
