"""LoRA opcional en un entorno independiente. Nunca carga test ni publica modelos."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def verificar_dataset(carpeta):
    carpeta = Path(carpeta)
    manifiesto = json.loads((carpeta / 'manifest.json').read_text(encoding='utf-8'))
    nombres = {'train.jsonl', 'validacion.jsonl', 'test.jsonl'}
    if set(manifiesto['archivos']) != nombres:
        raise ValueError('Manifiesto incompleto.')
    grupos = set()
    for nombre in sorted(nombres):
        archivo = carpeta / nombre
        if hashlib.sha256(archivo.read_bytes()).hexdigest() != manifiesto['archivos'][nombre]:
            raise ValueError('Dataset modificado después de exportar: ' + nombre)
        actuales = set()
        for linea in archivo.read_text(encoding='utf-8').splitlines():
            mensajes = json.loads(linea)['messages']
            if len(mensajes) != 2 or [m['role'] for m in mensajes] != ['user', 'assistant']:
                raise ValueError('Formato conversacional no válido.')
            actuales.add(' '.join(mensajes[0]['content'].lower().split()))
        if not actuales or actuales & grupos:
            raise ValueError('Partición vacía o contaminación entre train/validación/test.')
        grupos |= actuales
    return manifiesto


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', required=True)
    p.add_argument('--base', default='Qwen/Qwen2.5-0.5B-Instruct')
    p.add_argument('--salida', required=True)
    p.add_argument('--solo-validar', action='store_true')
    p.add_argument('--cpu', action='store_true', help='Entrenamiento experimental lento, float32 sin cuantización')
    a = p.parse_args()
    datos = Path(a.dataset).resolve()
    manifiesto = verificar_dataset(datos)
    if a.solo_validar:
        print(json.dumps({'dataset_valido': True, 'cantidades': manifiesto['cantidades']}))
        return
    if sys.version_info[:2] not in ((3, 10), (3, 11), (3, 12)):
        raise ValueError('Usa un entorno separado Python 3.10–3.12 con requirements-train.txt.')
    salida = Path(a.salida).resolve()
    if salida.exists():
        raise ValueError('La salida debe ser una carpeta nueva; no se sobrescriben candidatos.')
    try:
        import torch
        from datasets import load_dataset
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from peft import LoraConfig
        from trl import SFTConfig, SFTTrainer
    except ImportError:
        raise ValueError('Faltan dependencias del entorno de entrenamiento; consulta requirements-train.txt.') from None
    if not a.cpu and not torch.cuda.is_available():
        raise ValueError('CUDA no disponible. Usa otra máquina con NVIDIA o --cpu para una prueba pequeña y lenta.')
    kwargs = {'trust_remote_code': False}
    if a.cpu:
        kwargs.update(torch_dtype=torch.float32)
    else:
        kwargs.update(device_map={'': 0}, quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.float16))
    tokenizer = AutoTokenizer.from_pretrained(a.base, trust_remote_code=False)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    modelo = AutoModelForCausalLM.from_pretrained(a.base, **kwargs)
    if not a.cpu:
        from peft import prepare_model_for_kbit_training
        modelo = prepare_model_for_kbit_training(modelo)
    dataset = load_dataset('json', data_files={
        'train': str(datos / 'train.jsonl'), 'validation': str(datos / 'validacion.jsonl')})
    trainer = SFTTrainer(model=modelo, processing_class=tokenizer,
        train_dataset=dataset['train'], eval_dataset=dataset['validation'],
        peft_config=LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05,
                               target_modules='all-linear', task_type='CAUSAL_LM'),
        args=SFTConfig(output_dir=str(salida), num_train_epochs=1, max_length=256,
            per_device_train_batch_size=1, per_device_eval_batch_size=1,
            gradient_accumulation_steps=4, learning_rate=0.0001, report_to='none',
            eval_strategy='epoch', save_strategy='epoch', save_total_limit=1,
            gradient_checkpointing=True, use_cpu=a.cpu, bf16=False, fp16=not a.cpu,
            seed=42, data_seed=42))
    trainer.train()
    metricas = trainer.evaluate()
    trainer.save_model(str(salida))
    tokenizer.save_pretrained(str(salida))
    (salida / 'lara_entrenamiento.json').write_text(json.dumps({
        'base': a.base, 'dataset': manifiesto, 'metricas_validacion': metricas,
        'activado': False, 'nota': 'Adaptador candidato. Registrar en el gateway y evaluar antes de promover.'
    }, indent=2), encoding='utf-8')
    print('Adaptador guardado. No se ha cambiado el modelo de LARA.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as e:
        print('Error: ' + str(e))
        sys.exit(1)
