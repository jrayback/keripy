# -*- encoding: utf-8 -*-
"""
KERI
keri.app.forwarding module

module for enveloping and forwarding KERI message
"""
import random
from ordered_set import OrderedSet as oset

from hio.base import doing
from hio.help import decking, ogler

from keri import kering
from keri.app import agenting
from keri.app.habbing import GroupHab
from keri.core import coring, eventing
from keri.db import dbing
from keri.kering import Roles
from keri.peer import exchanging

logger = ogler.getLogger()


class Poster(doing.DoDoer):
    """
    DoDoer that wraps any KERI event (KEL, TEL, Peer to Peer) in a /fwd `exn` envelope and
    delivers them to one of the target recipient's witnesses for store and forward
    to the intended recipient

    """

    def __init__(self, hby, mbx=None, evts=None, cues=None, klas=None, **kwa):
        self.hby = hby
        self.mbx = mbx
        self.evts = evts if evts is not None else decking.Deck()
        self.cues = cues if cues is not None else decking.Deck()
        self.klas = klas if klas is not None else agenting.HTTPMessenger

        doers = [doing.doify(self.deliverDo)]
        super(Poster, self).__init__(doers=doers, **kwa)

    def deliverDo(self, tymth=None, tock=0.0):
        """
        Returns:  doifiable Doist compatible generator method that processes
                   a queue of messages and envelopes them in a `fwd` message
                   and sends them to one of the witnesses of the recipient for
                   store and forward.

        Usage:
            add result of doify on this method to doers list
        """

        # enter context
        self.wind(tymth)
        self.tock = tock
        _ = (yield self.tock)

        while True:
            while self.evts:
                evt = self.evts.popleft()
                src = evt["src"]
                recp = evt["dest"]
                tpc = evt["topic"]
                srdr = evt["serder"]
                atc = evt["attachment"] if "attachment" in evt else None

                # Get the hab of the sender
                if "hab" in evt:
                    hab = evt["hab"]
                else:
                    hab = self.hby.habs[src]

                ends = self.endsFor(hab, recp)
                try:
                    if Roles.controller in ends:
                        yield from self.sendDirect(hab, ends[Roles.controller], serder=srdr, atc=atc)
                    elif Roles.agent in ends:
                        yield from self.sendDirect(hab, ends[Roles.agent], serder=srdr, atc=atc)
                    elif Roles.mailbox in ends:
                        yield from self.forward(hab, ends[Roles.mailbox], recp=recp, serder=srdr, atc=atc, topic=tpc)
                    elif Roles.witness in ends:
                        yield from self.forward(hab, ends[Roles.witness], recp=recp, serder=srdr, atc=atc, topic=tpc)
                    else:
                        logger.info(f"No end roles for {recp} to send evt={recp}")
                        continue
                except kering.ConfigurationError as e:
                    logger.error(f"Error sending to {recp} with ends={ends}.  Err={e}")
                    continue
                # Get the kever of the recipient and choose a witness

                self.cues.append(dict(dest=recp, topic=tpc, said=srdr.said))

                yield self.tock

            yield self.tock

    def send(self, dest, topic, serder, src=None, hab=None, attachment=None):
        """
        Utility function to queue a msg on the Poster's buffer for
        enveloping and forwarding to a witness

        Parameters:
            src (str): qb64 identifier prefix of sender
            hab (Hab): Sender identifier habitat
            dest (str) is identifier prefix qb64 of the intended recipient
            topic (str): topic of message
            serder (Serder) KERI event message to envelope and forward:
            attachment (bytes): attachment bytes

        """
        src = src if src is not None else hab.pre

        evt = dict(src=src, dest=dest, topic=topic, serder=serder)
        if attachment is not None:
            evt["attachment"] = attachment
        if hab is not None:
            evt["hab"] = hab

        self.evts.append(evt)

    def sendEvent(self, hab, fn=0):
        """ Returns generator for sending event and waiting until send is complete """
        # Send KEL event for processing
        icp = self.hby.db.cloneEvtMsg(pre=hab.pre, fn=fn, dig=hab.kever.serder.saidb)
        ser = coring.Serder(raw=icp)
        del icp[:ser.size]

        sender = hab.mhab.pre if isinstance(hab, GroupHab) else hab.pre
        self.send(src=sender, dest=hab.kever.delegator, topic="delegate", serder=ser, attachment=icp)
        while True:
            if self.cues:
                cue = self.cues.popleft()
                if cue["said"] == ser.said:
                    break
                else:
                    self.cues.append(cue)
            yield self.tock

    @staticmethod
    def endsFor(hab, dest):
        ends = dict()

        for (_, erole, eid), end in hab.db.ends.getItemIter(keys=(dest,)):
            locs = dict()
            urls = hab.fetchUrls(eid=eid, scheme="")
            for rscheme, url in urls.firsts():
                locs[rscheme] = url

            if erole not in ends:
                ends[erole] = dict()

            ends[erole][eid] = locs

        ends[Roles.witness] = dict()
        if kever := hab.kevers[dest] if dest in hab.kevers else None:
            # latest key state for cid
            for eid in kever.wits:
                locs = dict()
                urls = hab.fetchUrls(eid=eid, scheme="")
                for rscheme, url in urls.firsts():
                    locs[rscheme] = url

                ends[Roles.witness][eid] = locs

        return ends

    def sendDirect(self, hab, ends, serder, atc):
        ctrl, locs = random.choice(list(ends.items()))
        witer = agenting.messengerFrom(hab=hab, pre=ctrl, urls=locs)

        msg = bytearray(serder.raw)
        if atc is not None:
            msg.extend(atc)

        witer.msgs.append(bytearray(msg))  # make a copy
        self.extend([witer])

        while not witer.idle:
            _ = (yield self.tock)

        self.remove([witer])

    def forward(self, hab, ends, recp, serder, atc, topic):
        # If we are one of the mailboxes, just store locally in mailbox
        owits = oset(ends.keys())
        if self.mbx and owits.intersection(hab.prefixes):
            msg = bytearray(serder.raw)
            if atc is not None:
                msg.extend(atc)
            self.mbx.storeMsg(topic=f"{recp}/{topic}".encode("utf-8"), msg=msg)
            return

        # Its not us, randomly select a mailbox and forward it on
        mbx, mailbox = random.choice(list(ends.items()))
        msg = bytearray()
        msg.extend(introduce(hab, mbx))

        # create the forward message with payload embedded at `a` field
        fwd = exchanging.exchange(route='/fwd', modifiers=dict(pre=recp, topic=topic),
                                  payload=serder.ked)
        ims = hab.endorse(serder=fwd, last=True, pipelined=False)

        # Transpose the signatures to point to the new location
        if atc is not None:
            pathed = bytearray()
            pather = coring.Pather(path=["a"])
            pathed.extend(pather.qb64b)
            pathed.extend(atc)
            ims.extend(coring.Counter(code=coring.CtrDex.PathedMaterialQuadlets,
                                      count=(len(pathed) // 4)).qb64b)
            ims.extend(pathed)

        witer = agenting.messengerFrom(hab=hab, pre=mbx, urls=mailbox)

        msg.extend(ims)
        witer.msgs.append(bytearray(msg))  # make a copy
        self.extend([witer])

        while not witer.idle:
            _ = (yield self.tock)



class ForwardHandler(doing.Doer):
    """
    Handler for forward `exn` messages used to envelope other KERI messages intended for another recipient.
    This handler acts as a mailbox for other identifiers and stores the messages in a local database.

    on
        {
           "v": "KERI10JSON00011c_",                               // KERI Version String
           "t": "exn",                                             // peer to peer message ilk
           "dt": "2020-08-22T17:50:12.988921+00:00"
           "r": "/fwd",
           "q": {
              "pre": "EEBp64Aw2rsjdJpAR0e2qCq3jX7q7gLld3LjAwZgaLXU",
              "topic": "delegate"
            }
           "a": '{
              "v":"KERI10JSON000154_",
              "t":"dip",
              "d":"Er4bHXd4piEtsQat1mquwsNZXItvuoj_auCUyICmwyXI",
              "i":"Er4bHXd4piEtsQat1mquwsNZXItvuoj_auCUyICmwyXI",
              "s":"0",
              "kt":"1",
              "k":["DuK1x8ydpucu3480Jpd1XBfjnCwb3dZ3x5b1CJmuUphA"],
              "n":"EWWkjZkZDXF74O2bOQ4H5hu4nXDlKg2m4CBEBkUxibiU",
              "bt":"0",
              "b":[],
              "c":[],
              "a":[],
              "di":"Et78eYkh8A3H9w6Q87EC5OcijiVEJT8KyNtEGdpPVWV8"
           }
        }-AABAA1o61PgMhwhi89FES_vwYeSbbWnVuELV_jv7Yv6f5zNiOLnj1ZZa4MW2c6Z_vZDt55QUnLaiaikE-d_ApsFEgCA

    """

    resource = "/fwd"

    def __init__(self, hby, mbx, cues=None, **kwa):
        """

        Parameters:
            mbx (Mailboxer): message storage for store and forward
            formats (list) of format str names accepted for offers
            cues (Optional(decking.Deck)): outbound cue messages

        """
        self.hby = hby
        self.msgs = decking.Deck()
        self.cues = cues if cues is not None else decking.Deck()
        self.mbx = mbx

        super(ForwardHandler, self).__init__(**kwa)

    def do(self, tymth, tock=0.0, **opts):
        """ Handle incoming messages by parsing and verifiying the credential and storing it in the wallet

        Parameters:
            tymth (function): injected function wrapper closure returned by .tymen() of
                Tymist instance. Calling tymth() returns associated Tymist .tyme.
            tock (float): injected initial tock value

        Messages:
            payload is dict representing the body of a /credential/issue message
            pre is qb64 identifier prefix of sender
            sigers is list of Sigers representing the sigs on the /credential/issue message
            verfers is list of Verfers of the keys used to sign the message

        """
        # start enter context
        self.wind(tymth)
        self.tock = tock
        yield self.tock

        while True:
            while self.msgs:
                msg = self.msgs.popleft()
                payload = msg["payload"]
                modifiers = msg["modifiers"]
                attachments = msg["attachments"]

                recipient = modifiers["pre"]
                topic = modifiers["topic"]
                resource = f"{recipient}/{topic}"

                pevt = bytearray()
                for pather, atc in attachments:
                    ked = pather.resolve(payload)
                    sadder = coring.Sadder(ked=ked, kind=eventing.Serials.json)
                    pevt.extend(sadder.raw)
                    pevt.extend(atc)

                if not pevt:
                    print("error with message, nothing to forward", msg)
                    continue

                self.mbx.storeMsg(topic=resource, msg=pevt)
                yield self.tock

            yield self.tock


def introduce(hab, wit):
    """ Clone and return hab KEL if lastest event has not been receipted by wit

    Check to see if the target witness has already provided a receipt for the latest event
    for the identifier of hab, clone the KEL and return it as a bytearray so it can be sent to
    the target.

    Parameters:
        hab (Hab): local environment for the identifier to propagate
        wit (str): qb64 identifier prefix of the recipient of KEL if not already receipted

    Returns:
        bytearray: cloned KEL of hab

    """
    msgs = bytearray()
    if wit in hab.kever.wits:
        return msgs

    iserder = hab.kever.serder
    witPrefixer = coring.Prefixer(qb64=wit)
    dgkey = dbing.dgKey(wit, iserder.said)
    found = False
    if witPrefixer.transferable:  # find if have rct from other pre for own icp
        for quadruple in hab.db.getVrcsIter(dgkey):
            if bytes(quadruple).decode("utf-8").startswith(hab.pre):
                found = True  # yes so don't send own inception
    else:  # find if already rcts of own icp
        for couple in hab.db.getRctsIter(dgkey):
            if bytes(couple).decode("utf-8").startswith(hab.pre):
                found = True  # yes so don't send own inception

    if not found:  # no receipt from remote so send own inception
        # no vrcs or rct of own icp from remote so send own inception
        for msg in hab.db.clonePreIter(pre=hab.pre):
            msgs.extend(msg)

        msgs.extend(hab.replyEndRole(cid=hab.pre))
    return msgs
