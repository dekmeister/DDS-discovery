# Lab01

## Program

### Station

A station is a publisher with a writer that reports multiple vessels. Each vessel is an instance (an independent record) on the VesselReport topic. The instance is not explicitly handled in the program, instead the instance is created whenever a new report with a new key is made.

The station is also capable of indicating instances are no longer being reported on by the writer with leave (DDS unregister) and the entities are no longer valid with bogus (DDS dispose). The station (and therefore writer as an entitiy owned by the station process) are also killed.

### Watcher

A watcher is a reader that is intended to make the ending of an instance visible. It can be run in live (block until data arrives, then take repeatedly until drained, then block again), read probe (do nothing for 6 s, then read five times) and take (do nothing for 6 s, then take once, then read once). 
NB: these differ from the pure DDS read and take actions and live is not a standard DDS action at all. They are intended to demonstrate aspects for the lab only.

The watcher retains a record for each vessel (instance) it has observed.

## DDS Data Entities

- Domain: The broadest set of an isolated set of data. A domain is joined, not created. Defined by an integer. Similar to a database (although unlike a database there is no central management).
- DomainParticipant: Membership of a process within a single domain. Typically each process with a DDS thread is a DomainParticipant. It owns the other entities made. Each DomainParticipant has a unique GUID. 
    - A process can create several participants in different domains if required.
- Topic: A named, typed channel inside one domain. The identity is domain-wide, however the object is owned by a participant. Defined by a name and type (which defines the key that identifies unique instances within the topic). Similar to a table in a database (the type is the database schema).
    - Unkeyed types are possible and have only one instance for the entire topic.
- Instance (also called record): One distinct key value within one topic. Lives in the topic and not in any single writer (i.e. any two entities with the same key in a topic are the same entity regardless of which writer made it). Similar to a row in a database table.
- Sample: One write of an instance. Held in caches and subject to history. Each sample is a separate observation. Similar to an update to a database row.

### Multiple Views of the Data

Each DomainParticipant holds its own copy of everything it cares about (e.g. for a reader what it has subscribed to). There is no "real" version of the above, only different views from different participants. The view of the reader (the instance state it is tracking) is derived, not shared and scoped to entities holding it only. 

### Ending Instances

An instance can end in several different ways that imply different things:
- dispose: Indicate that the instance no longer exists and should be deleted.
- unregister: Indicate that the writer previously publishing the instance no longer is the source. It is NOT making a claim about the actual instance, but this writer is no longer claiming to be a source.
    - The autodispose Boolean inside the WRITER_DATA_LIFECYCLE QoS policy indicates what an unregistered instance means. The writer uses it to indicate if an unregistered instance should be disposed of (true) or not (false). It is also intended as the the default for readers to use for a writer that died.

There is no explicit ending to the instance if the writer stops reporting. This is not announced, but can be inferred from other states of DDS processes.

None of these are permanent in DDS (DDS has a soft state that is continuously refreshed by it source and begins to decay when the refresh stops) and the instances are the readers understanding of the world. If a writer reports the instance again it appears again.

## Findings

- Each instance has its own sample and is carried by the reader. KEEP_LAST(1) maintains 3 different samples (one for each vessel instance).
- read() leaves the sample in the cache, while take() removes it. When performing a take(), the instances still exist, but the samples are removed.
- There are only two terminal states: NotAliveDisposed (any writer disposed of it - including with autodispose as True) and NotAliveNoWriters (no writers are registered for it).  Instances ending in a given terminal state are indistinguishable. Endings arrive as samples with no valid data — key only. Killing the writer process resulted in no instance-state change and the reader only noticed when the DEADLINE (a contract about rate on a per instance basis as QoS) expires, although the specific CycloneDDS behaviour here is unclear.

