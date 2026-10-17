import asyncio
import os
import signal
import sys
import textwrap


async def test_sigterm_shuts_down_cleanly_without_external_network():
    script = textwrap.dedent("""
        import asyncio
        import voice_notify.main as runtime
        from voice_notify.config import Config

        class OfflineClient:
            def __init__(self, config, sender):
                self.failure = asyncio.get_running_loop().create_future()
                self.tracker = self
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def start(self, token):
                print("ready", flush=True)
                await asyncio.Event().wait()
            async def close(self):
                print("closed", flush=True)
            def pause(self):
                pass

        runtime.VoiceClient = OfflineClient
        config = Config("fake.discord.token", "123:fake", -100, 100, frozenset({200}))
        asyncio.run(runtime.serve(config))
    """)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert await asyncio.wait_for(process.stdout.readline(), timeout=5) == b"ready\n"
        os.kill(process.pid, signal.SIGTERM)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
        assert process.returncode == 0, stderr.decode()
        assert b"closed" in stdout
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
