import { z } from 'zod';
import { jsonResult } from './_format.js';
import * as core from '../core/alerts.js';

export function registerAlertTools(server) {
  server.tool(
    'alert_create',
    'Create an alert via the TradingView alert dialog. Supports Multi-condition alerts: passing both "price" and "volume" creates ONE alert with two conditions chained via "AND" (TradingView\'s "Add condition" flow). At least one of "price"/"volume" is required.',
    {
      condition: z.string().optional().describe('Legacy/default condition (informational). Use price_condition/volume_condition for per-leg control.'),
      price: z.coerce.number().optional().describe('Price level for the price leg.'),
      volume: z.coerce.number().optional().describe('Volume threshold for the volume leg. When combined with "price" the volume leg is added via the multi-condition dialog.'),
      price_condition: z.string().optional().describe('Condition for price leg, e.g. "Greater Than", "Less Than", "Crossing Up". Default: Crossing.'),
      volume_condition: z.string().optional().describe('Condition for volume leg, e.g. "Crossing Up", "Crossing Down". Volume supports only Crossing variants — Greater Than is not available. Default: Crossing.'),
      message: z.string().optional().describe('Custom alert message. Opens the Message popup, types the text and clicks Apply. Verified against the dialog button label after Apply.'),
    },
    async ({ condition, price, volume, message, price_condition, volume_condition }) => {
      try {
        return jsonResult(await core.create({ condition, price, volume, message, price_condition, volume_condition }));
      } catch (err) {
        return jsonResult({ success: false, error: err.message }, true);
      }
    }
  );

  server.tool('alert_list', 'List active alerts', {}, async () => {
    try { return jsonResult(await core.list()); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  server.tool('alert_delete', 'Delete all alerts or open context menu for deletion', {
    delete_all: z.coerce.boolean().optional().describe('Delete all alerts'),
  }, async ({ delete_all }) => {
    try { return jsonResult(await core.deleteAlerts({ delete_all })); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });
}
