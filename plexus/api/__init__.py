"""
The runtime's WebSocket half — see PROTOCOL.md.

Deliberately empty of imports. ``server`` imports ``session``, which imports
``console``; re-exporting any of them from here would make importing the
package import its own submodules mid-initialisation. Reach for what you need
by its own name:

    from plexus.api.server import create_app, serve
"""
