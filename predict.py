import os
import logging
import argparse

import torch
import torchmodels
import torch.nn as nn
import torch.utils.data as td

import utils
import models
import models.jlu
import dataset
import inference
from train import embeds


MODES = ["word", "label", "intent"]

parser = argparse.ArgumentParser(fromfile_prefix_chars="@")

group = parser.add_argument_group("Logging Options")
utils.add_logging_arguments(group, "predict")
group.add_argument("--argparse-filename",
                   type=str, default="predict.args")
group.add_argument("--show-progress", action="store_true", default=False)

group = parser.add_argument_group("Data Options")
group.add_argument("--word-path", type=str, required=True)
for mode in MODES:
    group.add_argument(f"--{mode}-vocab", type=str, required=True)
group.add_argument("--data-workers", type=int, default=8)
group.add_argument("--seed", type=int, default=None)
group.add_argument("--unk", type=str, default="<unk>")
group.add_argument("--eos", type=str, default="<eos>")
group.add_argument("--bos", type=str, default="<bos>")

group = parser.add_argument_group("Prediction Options")
group.add_argument("--ckpt-path", type=str, required=True)
group.add_argument("--batch-size", type=int, default=32)
group.add_argument("--save-dir", type=str, required=True)
group.add_argument("--top-k", type=int, default=1,
                   help="top-k candidates of labels and intents to consider")
for mode in MODES[1:]:
    group.add_argument(f"--{mode}-filename", type=str, default=f"{mode}.txt")
    group.add_argument(f"--{mode}-probs-filename",
                       type=str, default=f"{mode}-probs.txt")
group.add_argument("--beam-size", type=int, default=6)
group.add_argument("--expand-vocab", action="store_true", default=False)
embeds.add_embed_arguments(group)

group.add_argument("--gpu", type=int, action="append", default=[])

group = parser.add_argument_group("Model Options")
group.add_argument("--model-path", required=True)
group.add_argument("--hidden-dim", type=int, default=300)
group.add_argument("--word-dim", type=int, default=300)


def prepare_dataset(args, vocab):
    dset = dataset.TextSequenceDataset(
        paths=[args.word_path],
        feats=["string", "tensor"],
        vocabs=[vocab],
        pad_eos=args.eos,
        pad_bos=args.bos,
        unk=args.unk
    )
    return dset


def prepare_model(args, vocabs):
    torchmodels.register_packages(models)
    mdl_cls = torchmodels.create_model_cls(models.jlu, args.model_path)
    mdl = mdl_cls(
        hidden_dim=args.hidden_dim,
        word_dim=args.word_dim,
        num_words=len(vocabs[0]),
        num_slots=len(vocabs[1]),
        num_intents=len(vocabs[2])
    )
    mdl.reset_parameters()
    ckpt = torch.load(args.ckpt_path)
    mdl.load_state_dict(ckpt["model"])
    if args.expand_vocab:
        pass
        # mdl_vocab = vocabs[0]
        # mdl_emb = mdl.embeds[0].weight
        # emb = embeds.get_embeddings(args)
        # emb.preload()
        # emb = {w: v for w, v in emb}
        # for rword in [args.bos, args.eos, args.unk]:
        #     emb[rword] = mdl_emb[mdl_vocab.f2i.get(rword)].detach().numpy()
        # vocab = utils.Vocabulary()
        # utils.populate_vocab(emb.keys(), vocab)
        # mdl.embeds[0] = embedding.BasicEmbedding(
        #     vocab_size=len(vocab),
        #     dim=mdl.word_dim,
        #     allow_padding=True
        # )
        # embeds._load_embeddings(mdl.embeds[0], vocab, emb.items())
    else:
        vocab = vocabs[0]
    return mdl, vocab


class PredictorWithProgress(inference.Predictor):

    def __init__(self, *args, progress=False, **kwargs):
        super(PredictorWithProgress, self).__init__(*args, **kwargs)
        self.show_progress = progress

    def select_data(self, data):
        return data[0]

    def model_call(self, data):
        return self.model.predict(*data, self.topk)

    def on_run_started(self, dataloader):
        ret = super(PredictorWithProgress, self).on_run_started(dataloader)
        self.progress = utils.tqdm(
            total=len(dataloader.dataset),
            desc="predicting",
            disable=not self.show_progress
        )
        return ret

    def on_batch_started(self, batch):
        ret = super(PredictorWithProgress, self).on_batch_started(batch)
        self.progress.update(len(batch["string"]))
        return ret

    def on_run_finished(self, stats):
        ret = super(PredictorWithProgress, self).on_run_finished(stats)
        self.progress.close()
        return ret

    def predict(self, dataloader):
        return self.inference(dataloader)

# class Predictor(object):
#
#     def __init__(self, model, device, sent_vocab, label_vocab, intent_vocab,
#                  bos, eos, unk, tensor_key="tensor"):
#         self.model = model
#         self.device = device
#         self.sent_vocab = sent_vocab
#         self.label_vocab = label_vocab
#         self.intent_vocab = intent_vocab
#         self.vocabs = [self.sent_vocab, self.label_vocab, self.intent_vocab]
#         self.bos = bos
#         self.eos = eos
#         self.unk = unk
#         self.bos_idxs = [v.f2i.get(bos) for v in self.vocabs]
#         self.eos_idxs = [v.f2i.get(eos) for v in self.vocabs]
#         self.unk_idxs = [v.f2i.get(unk) for v in self.vocabs]
#         self.tensor_key = tensor_key
#
#     @property
#     def module(self):
#         if isinstance(self.model, nn.DataParallel):
#             return self.model.module
#         else:
#             return self.model
#
#     def to_sent(self, idx, vocab):
#         return " ".join(vocab.i2f.get(w, self.unk) for w in idx)
#
#     def validate(self, sent, labels, intent, length, w_prob, l_prob):
#         """validate a single instance of sample"""
#         words, labels = sent.split(), labels.split()
#         def ensure_enclosed(x):
#             return x[0] == self.bos and x[-1] == self.eos
#         if not ensure_enclosed(words) or not ensure_enclosed(labels):
#             return False
#         if len(words) != len(labels):
#             return False
#         return True
#
#     def prepare_batch(self, batch):
#         x, lens = batch[self.tensor_key][0]
#         x, lens = x.to(self.device), lens.to(self.device)
#         batch_size = x.size(0)
#         return batch_size, (x, lens)
#
#     def predict(self, dataloader):
#         vocabs = [self.sent_vocab, self.label_vocab, self.intent_vocab]
#         self.model.train(False)
#         progress = utils.tqdm(total=len(dataloader.dataset), desc="predicting")
#         preds = []
#         for batch in dataloader:
#             batch_size, (w, lens) = self.prepare_batch(batch)
#             progress.update(batch_size)
#             (labels, intents), (pl, pi) = self.model.predict(
#                 w, lens, self.bos_idxs[1],
#             )
#             labels, intents, lens, pl, pi = \
#                 [x.cpu().tolist() for x in [labels, intents, lens, pl[:, 0], pi]]
#             labels = [self.to_sent(label[:l], vocabs[1])
#                       for label, l in zip(labels, lens)]
#             intents = [self.to_sent([i], vocabs[2]) for i in intents]
#             preds.extend(list(zip(labels, intents, pl, pi)))
#         progress.close()
#         labels, intents, pl, pi = list(zip(*preds))
#         return (labels, intents), (pl, pi)


def save(args, labels, intents, pl, pi):
    labels = [" ".join(label.split()) for label in labels]
    pl, pi = [list(map(str, p)) for p in [pl, pi]]
    samples = [labels, intents, pl, pi]
    fnames = [args.label_filename, args.intent_filename,
              args.label_probs_filename, args.intent_probs_filename]
    paths = [os.path.join(args.save_dir, fn) for fn in fnames]
    for data, path in zip(samples, paths):
        with open(path, "w") as f:
            for sample in data:
                f.write(f"{sample}\n")


def report_stats(args, labels, intents, pl, pi):
    pass


def generate(args):
    devices = utils.get_devices(args.gpu)
    if args.seed is not None:
        utils.manual_seed(args.seed)

    logging.info("Loading data...")
    vocab_paths = [getattr(args, f"{mode}_vocab") for mode in MODES]
    vocabs = [utils.load_pkl(v) for v in vocab_paths]
    test_dataset = prepare_dataset(args, vocabs[0])
    test_dataloader = td.DataLoader(
        dataset=test_dataset,
        batch_size=args.batch_size,
        num_workers=args.data_workers,
        collate_fn=dataset.TextSequenceBatchCollator(
            pad_idxs=[len(v) for v in vocabs]
        )
    )

    logging.info("Initializing generation environment...")
    model, vocabs[0] = prepare_model(args, vocabs)
    model.beam_size = args.beam_size
    model = utils.to_device(model, devices)
    predictor = PredictorWithProgress(
        model=model,
        device=devices[0],
        vocabs=vocabs,
        progress=args.show_progress,
        bos=args.bos,
        eos=args.eos,
        unk=args.unk,
        topk=args.top_k
    )

    logging.info("Commencing prediction...")
    with torch.no_grad():
        (labels, pl), (intents, pi) = predictor.predict(test_dataloader)
    labels, intents = [l[0] for l in labels], [i[0] for i in intents]
    pl, pi = [p[0] for p in pl], [p[0] for p in pi]
    report_stats(args, labels, intents, pl, pi)
    save(args, labels, intents, pl, pi)

    logging.info("Done!")


if __name__ == "__main__":
    generate(utils.initialize_script(parser))