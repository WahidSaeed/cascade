from app import jobs


def test_jobs_cli(fresh_db, capsys):
    assert jobs.main(["discover"]) == 0 and capsys.readouterr().out.strip() == "[]"
    assert jobs.main(["drift"]) == 0 and capsys.readouterr().out.strip() == "[]"
    assert jobs.main(["nope"]) == 2
