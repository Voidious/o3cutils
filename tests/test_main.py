from main import main


def test_main(capsys):
    main()
    assert capsys.readouterr().out == "Hello from o3cutils!\n"
