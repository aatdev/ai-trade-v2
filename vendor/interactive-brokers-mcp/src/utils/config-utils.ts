import { promises as fs } from 'fs';
import path from 'path';

export class ConfigUtils {
  static async createTempConfigWithPort(gatewayDir: string, port: number): Promise<void> {
    const originalConfigPath = path.join(gatewayDir, 'clientportal.gw/root/conf.yaml');
    const tempConfigPath = path.join(gatewayDir, `clientportal.gw/root/conf-${port}.yaml`);

    try {
      // Read the original config
      const content = await fs.readFile(originalConfigPath, 'utf8');
      // Replace the port
      const updatedContent = content.replace(/listenPort:\s*\d+/, `listenPort: ${port}`);
      // Write to temp config file
      await fs.writeFile(tempConfigPath, updatedContent, 'utf8');
    } catch (error) {
      throw new Error(`Failed to create temporary config file: ${error}`);
    }
  }

  static async cleanupTempConfigFiles(gatewayDir: string): Promise<void> {
    try {
      const configDir = path.join(gatewayDir, 'clientportal.gw/root');
      const files = await fs.readdir(configDir);

      for (const file of files) {
        if (file.match(/^conf-\d+\.yaml$/)) {
          const filePath = path.join(configDir, file);
          await fs.unlink(filePath);
        }
      }
    } catch (error) {
      // Don't throw errors for cleanup failures - just log them
      console.warn(`Warning: Could not clean up temporary config files: ${error}`);
    }
  }
}
