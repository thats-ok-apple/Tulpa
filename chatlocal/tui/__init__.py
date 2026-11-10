"""Full-screen terminal frontend for the local Tulpa management API.

Textual is imported lazily so importing this package never requires it.
"""


def run(base_url=None, token_file=None, initial_instance=None):
    """Open the terminal UI. Closing it never stops the background service.

    base_url/token_file always address the hub (main) backend; initial_instance is an
    optional /instances descriptor selected at start while the hub stays the manager.
    """
    from .app import TulpaApp, default_client_factory
    TulpaApp(client_factory=default_client_factory(base_url, token_file), base_url=base_url,
             initial_instance=initial_instance).run()


__all__ = ['run']
