# Lab00

## Program

Both modules are structured in the same manner. The key elements being:
- Type Declaration: Defines the messages being sent. Mirroring idl/vts.idl and needs to be the same on both sides.
- Lookup Tables: Used to convert integers for various parameters (as used by cyclonedds) into their human readable equivalents. 
- Print Helpers: Print every output line with a timestamp for human readability and comparisions.
- Listener: Callback function 
- Main Loop: Differs depending on the type of module. Announce what's about to happen, build the objects, print the settings you were given, run the loop, print a summary. 

### Publisher

The Pub generates (writes) a vessel report (defined by the datatype derived from the interface spec) once a second.

### Subscriber

The Sub consumes (reads) a vessel report whenever one is available (event driven). A timer loop exists only to check if it is time to exit (outside of DDS). It uses take and empties the cache (take removes samples from that cache; read would leave them there and hand you the same ones again on the next pass). This is the subscriber cache only, the subscriber can't affect the publisher cache of this data.

## DDS

DDS (data distribution service) is a standard intended for real-time data exchange of applications in complex (lots of different elements), dynamic (elements can join or leave the network often) networks often found in distributed applications. It utilises a publish-subscribe model and enables a strong decoupling between publisher applications and subscriber applications. DDS provides the middleware that manages the actual transfer and is run as a library on each application separately (i.e. there is no central broker managing it). It is focussed on data and not messages.

### QOS

QOS defines the intended behaviour and use of the messages and ensures all parties are reasonably aligned. QOS are inherent in DDS specification and not specific to these labs.  There are 22 policies (in the base DDS standard - although this can be increased with vendor extensions) with a range of acceptable values and readers/writes (subs/pubs) must be compatible (not the same as identical) before they exchange data.

## cyclonedds

CycloneDDS is the middleware. It is running as a background thread (through the linked library) for both the separate reader AND the writer application processes. They both bind themselves to the same port (as opposed to a client server relationship where one process binds the well-known port and all other processes connect to it).

It is providing discovery (how the two processes find each other with no addresses configured. They announce themselves on the domain and listen for others), matching (deciding whether a particular reader and writer should exchange data: same topic name, same type, compatible QoS), the QoS state machine (tracking those contracts once matched, and raising a status when something changes or is violated), instance lifecycle (tracking each ship separately by mmsi, including when one is new, and when its writer disposes it or disappears) and wire (serialising your object to bytes, sending it, receiving and rebuilding it).

`cyclonedds ls` provides the entity view and `cyclonedds ps` provides the process view.

## Findings

- CycloneDDS generates default QoS values as (note that reliability is asymmetric, but compatible):

QoS | Writer | Reader
-- | -- | --
Reliability | RELIABLE (100 ms max block) | BEST_EFFORT
Durability | VOLATILE | VOLATILE
History | KEEP_LAST(1) | KEEP_LAST(1)
Ownership | SHARED | SHARED
Deadline | infinite | infinite

- The late joiner receives no messages from before. The writer has the HISTORY (how much is kept) set to KEEP_LAST(1) (and therefore it holds one value), but the DURABILITY (who is entitled to kept messages) is VOLATILE (deliver only to readers I already know about) and therefore won't send the kept message when the reader first joins. TRANSIENT_LOCAL (supported by a KEEP_LAST(n) history depth) would keep messages for late joiners.
- Which process is the broker/server: None. Both the reader and the writer are separate applications and each have their own instance of the middleware, running inside the application (that send/receives data and manages its own cache) and the only way for data to be shared is to move over the network between these two processes.