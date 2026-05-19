# UI Team Report

Daniel Scott - 018278104

Jianan Peng - 019098898

## Intro to PeerChat Project
PeerChat is a peer-to-peer distributed chat system built as a multi-module class project.
The full system combines:
- Peer membership/discovery
- Message distribution
- Message history/recovery
- Security (signing/encryption)
- Web UI layer

Together, these modules provide decentralized chat messaging, dynamic peer join/leave handling, and integrated transport/security flows across nodes. The project can be found at: https://github.com/thatoneguy01/PeerChat

## Intro to the UI Module
The UI team owns the user-facing chat application under `ui/`, implemented with Flask + Jinja/HTMX.
Our scope includes:
- Chat page rendering and interaction flow
- Connect/disconnect UX
- Message posting and message list refresh
- User list display and status rendering
- Service-layer hooks to call distribution/discovery/history/security-backed behaviors

## UI Module’s Relationship to the Project
The UI is the entry point where users interact with the distributed system:
- User actions in UI (connect, send message, disconnect) trigger backend service calls
- Services calls hand off to discovery and distribution modules
- Incoming messages and membership updates are rendered back into UI partials

UI is not just presentation; it is the orchestration boundary between human input and all core distributed modules. The UI services module is one of two major locations of integration between all the modules. While most of the creation and configuration integration takes place in the main module, the UI service module contains functions for all the main activity triggers and callbacks for the entire system. This works well for the project because the UI module is either the start or the end of every functional flow.

## Implementation Details
For the UI module, there were 2 main design parameters we needed to account for. First, the UI module needed to be able to communicate with the rest of the module easily. Second, the UI model needed to be able to render information updates in real time. One solution we considered was using a python native UI library. We decided against this because neither member of the team had experience with any of the 3rd party libraries, and the built in UI tools for python are limited and clunky. Instead, we decided to build a web UI and host it locally with Flask. Using a flask backend allowed easy connection to the rest of the python based project, and creating a web UI is easy and clean. All that was left was to solve the problem of realtime updates. Baseline Flask+Jinja+HTML doesn’t allow dynamic updates without refreshing the page, so we incorporated HTMX. The HTMX framework allows for dynamic updates to sub-files of HTML called partials. This allowed us to achieve the second requirement by moving connection, active user list, and message display into partials so they could be dynamically loaded.

Below is a more detailed breakdown based on UI commit history and current implementation:

### App/routing layer (`ui/app.py`)
Flask app factory, page routes, partial refresh routes, and form handlers.
Connection state management (connected, username, ip).
HTMX-friendly partial rendering for users/messages/connect state.

### Service layer (`ui/services/service.py`)
Connects UI actions to distributed runtime, including discovery node startup/bootstrap, membership subscription handling, peer registry updates, and history replay fetch/render updates.
Handles membership events such as JOIN_ACCEPTED and LEAVE_CONFIRMED.
Handles inbound message append and UI refresh callbacks.

### Feature/UI polish work
Message composer behavior, including keyboard shortcuts.
Draft save/restore per user.
Theme toggle with local storage.
Message alignment/rendering improvements.
Header branding/icon updates.
Message loading and display fixes, including sender naming.

## Integration with Other Modules
As stated before, the UI is one of 2 main integration hubs for the project. Below are summaries of the integrations between the UI and other modules:

### Peer Discovery
UI service starts/stops discovery node during connect/disconnect.
Membership events are consumed to keep active peers and user list in sync.

### Message Distribution
UI service bridges to distribution-facing peer registry and message path.
Chat send/receive behavior is tied to distribution-compatible message flow.

### Message History
UI service pulls recent history and rehydrates message list on connect.
History handling is coordinated during membership and replay paths.

### Security
UI service includes hooks such as prepare_message and key propagation paths to align with secure message processing flow.

## Work Distribution

### Jianan Peng
Built core UI product experience:
Implemented user roster and room-centric chat interaction flow,
Built chat service abstractions with mock-data mode for local/dev testing,
Added keyboard-friendly composer with per-user draft save/restore,
Refactored templates and responsive styles for clearer message rendering across screen sizes,
Shipped user-facing polish features including theme toggle persistence, brand mark, and favicon integration.
Improved chat template structure to support cleaner HTMX partial updates and easier UI maintenance.
Strengthened message rendering quality with better visual hierarchy and alignment across sender/receiver states.

### Daniel Scott
Built backend-hosted UI foundation and integration backbone:
Established the Flask UI skeleton and hosting direction used by the final runtime path,
Structured service exposure and implemented UI integration with message distribution + peer discovery flows,
Delivered integration-driven fixes for connect bootstrap, message loading/refresh behavior, and sender-name rendering,
Improved disconnect consistency so UI state and backend service state remain aligned during session teardown.

## Conclusion
This module successfully met both goals of building a working, well-functioning client interface for the PeerChat system, and acting as a key integration boundary for the project. Flask, Jinja, and HTMX combined allowed for the development of a thin web-based client which integrated tightly into the Python backend, yet still allowed for responsive, real-time updates without needing a full frontend framework and associated runtime stack.

The finished implementation satisfied the initial, overarching project goals of enabling users to connect to the distributed system, message peers, view the active peer membership list, and disconnect from the network with ease of use. However, beyond this presentation functionality, the UI service layer evolved into an important central coordination process for the application, mediating communications between peer discovery, message dispatch, message history storage, and security handling processes. Nearly all user-driven actions begin or end within this module, so it became a logical place for runtime coordination and handling of callbacks throughout the system.

Seth's work emphasizes end-user interaction quality and UI features, while Daniel's work emphasizes framework setup, module integration, and runtime stability. Combined, these contributions make the UI a functional and reliable front door to the full P2P architecture.
