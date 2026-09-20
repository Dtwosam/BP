from bp_engine.execution.cli import main

if __name__ == "__main__":
    raise SystemExit(
        main(
            [
                "--prediction-version",
                "live-prediction-v1",
                "--execution-version",
                "paper-execution-v1",
                "--poll-seconds",
                "5",
            ]
        )
    )
