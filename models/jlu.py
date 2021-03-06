import torch
import torch.nn.functional as F
import torchmodels
from torchmodels.modules import rnn
from torchmodels.modules import pooling
from torchmodels.modules import embedding

from . import classifier
from . import utils


class AbstractJointLU(torchmodels.Module):

    def __init__(self, hidden_dim, word_dim, num_words, num_slots, num_intents):
        super(AbstractJointLU, self).__init__()
        self.hidden_dim = hidden_dim
        self.word_dim = word_dim
        self.num_words = num_words
        self.num_slots = num_slots
        self.num_intents = num_intents
        self.vocab_sizes = [num_words, num_slots, num_intents]

    def embeddings(self):
        """
        Get embeddings object
        :return (torchmodels.modules.embedding.AbstractEmbedding): embeddings
        """
        raise NotImplementedError()

    def forward(self, w, s, lens):
        raise NotImplementedError()

    def predict(self, w, lens, topk=10, **kwargs):
        """
        Predicts and returns the most likely candidates for slots and intents
        :param w:
        :param lens:
        :param topk:
        :return: ((slot_candidates, slot_probs), \
                  (intent_candidates, intent_probs))

            tuple(tuple([batch_size x topk x max_len] LongTensor, \
                        [batch_size x topk] FloatTensor), \
                  tuple([batch_size x topk] LongTensor, \
                        [batch_size x topk] FloatTensor))
        """
        raise NotImplementedError()


class RNNBasedJLU(AbstractJointLU):

    name = "rnn-jlu"

    def __init__(self, *args,
                 embedding=embedding.AbstractEmbedding,
                 rnn=rnn.AbstractRNNCell,
                 pooling=pooling.AbstractPooling,
                 slot_classifier=classifier.AbstractClassifier,
                 intent_classifier=classifier.AbstractClassifier, **kwargs):
        super(RNNBasedJLU, self).__init__(*args, **kwargs)
        self.embedding_cls = embedding
        self.rnn_cls = rnn
        self.pooling_cls = pooling
        self.slot_classifier_cls = slot_classifier
        self.intent_classifier_cls = intent_classifier

        self.embeds = self.embedding_cls(
            vocab_size=self.num_words,
            dim=self.word_dim
        )
        self.rnn = self.rnn_cls(
            input_dim=self.word_dim,
            hidden_dim=self.hidden_dim
        )
        self.pooling = self.pooling_cls(
            dim=self.hidden_dim
        )
        self.slot_classifier = self.slot_classifier_cls(
            hidden_dim=self.hidden_dim,
            num_labels=self.num_slots + 1
        )
        self.intent_classifier = self.intent_classifier_cls(
            hidden_dim=self.hidden_dim,
            num_labels=self.num_intents + 1
        )

    def embeddings(self):
        return self.embeds

    def _forward_intent(self, hs):
        return self.intent_classifier(self.pooling(hs))

    def _forward_slots(self, hs):
        hs_size = hs.size()
        hs = hs.view(-1, hs_size[-1])
        o = self.slot_classifier(hs)
        return o.view(*hs_size[:-1], o.size(-1))

    def forward(self, w, s, lens):
        w = self.embeds(w)
        hs, _, h = self.rnn(w, lens)
        igts = self.intent_classifier(self.pooling(hs))
        lgts = self._forward_slots(hs)
        return lgts, igts

    def predict(self, w, lens, topk=10, **kwargs):
        lgts, igts = self.forward(w, None, lens)
        slot_logprobs = F.log_softmax(lgts, 2)
        bs = utils.BeamSearchDecoder(slot_logprobs, lens, topk)
        slot_candidates, slot_logprobs = bs.decode()
        slot_probs = slot_logprobs.exp()
        intent_probs, intent_candidates = \
            torch.sort(F.softmax(igts, 1), 1, True)
        intent_probs = intent_probs[:, :topk]
        intent_candidates = intent_candidates[:, :topk]
        return (slot_candidates, slot_probs), \
               (intent_candidates, intent_probs)