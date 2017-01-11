#The COPYRIGHT file at the top level of this repository contains the full
#copyright notices and license terms.

from trytond.pool import Pool
import purchase


def register():
    Pool.register(
        purchase.Party,
        purchase.Product,
        purchase.Purchase,
        purchase.PurchaseLine,
        purchase.FedicomLog,
        module='purchase_fedicom', type_='model')
